"""Routine hebdomadaire : découverte, critères stricts, Jev, installation
plafonnée (spec § Routine). Fermeture en échec : dans le doute, on n'installe pas."""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import (config, github, inspection, install, profile as profile_mod, report,
               sources, trust, verdict)

_HELD: dict[str, int] = {}          # chemin du verrou -> descripteur qui le tient
_HELD_GUARD = threading.Lock()


@dataclass
class RunResult:
    run_id: str
    status: str = "ok"
    dry_run: bool = False
    candidates: int = 0
    jev_calls: int = 0
    jev_status: str = ""
    installed: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)
    upstream_changed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def run_id_of(now: float) -> str:
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _try_os_lock(fd: int) -> bool:
    """Verrou exclusif non bloquant tenu par le système d'exploitation."""
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_lock(path: Path) -> bool:
    """Verrou exclusif tenu par le système d'exploitation pendant toute
    l'exécution. Il disparaît avec le processus, même tué : aucun verrou
    orphelin ne peut bloquer la routine, et le fichier n'est jamais supprimé
    (le supprimer rouvrirait une course). Ne lève jamais : toute erreur vaut
    « déjà pris »."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
    except OSError:
        return False
    if not _try_os_lock(fd):
        os.close(fd)
        return False
    with _HELD_GUARD:
        _HELD[str(path)] = fd
    return True


def release_lock(path: Path) -> None:
    with _HELD_GUARD:
        fd = _HELD.pop(str(path), None)
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass                       # la fermeture ci-dessous libère de toute façon
    os.close(fd)


def discover(prof: profile_mod.Profile) -> tuple[list[dict], list[str], bool]:
    """Requêtes thématiques (source principale) puis classements (complément).
    Une source en panne est notée, jamais bloquante, quelle que soit l'erreur.
    Renvoie (candidats, pannes, toutes les recherches thématiques en panne)."""
    found: dict[tuple[str, str], dict] = {}
    errors: list[str] = []

    def add(c: dict) -> None:
        key = (c["source"].lower(), c["skill_id"].lower())
        if key not in found or c["installs"] > found[key]["installs"]:
            found[key] = c
    themes = prof.meta_themes + prof.stack_themes
    searched = 0
    for theme in themes:
        try:
            for c in sources.search_skills(theme, limit=prof.per_query):
                add(c)
            searched += 1
        except sources.SearchError as e:
            errors.append(f"recherche « {theme} » : {e}")
        except Exception as e:          # noqa: BLE001 — une réponse étrange, pas un arrêt
            errors.append(f"recherche « {theme} » : {type(e).__name__} : {e}")
    if prof.leaderboard_top:
        for kind in sources.LEADERBOARDS:
            try:
                for c in sources.fetch_leaderboard(kind, top=prof.leaderboard_top):
                    add(c)
            except sources.LeaderboardError as e:
                errors.append(f"classement {kind} : {e}")
            except Exception as e:      # noqa: BLE001
                errors.append(f"classement {kind} : {type(e).__name__} : {e}")
    search_down = bool(themes) and searched == 0
    return sorted(found.values(), key=lambda c: -c["installs"]), errors, search_down


def _label(row: dict) -> str:
    return f"{row['skill_id']} ({row['source']})"


def precheck(row: dict, prof: profile_mod.Profile, present: set[str]) -> str | None:
    """Critères d'installation peu coûteux, appliqués avant tout appel Jev (P5)."""
    owner = row["source"].split("/", 1)[0].lower()
    installs = row.get("installs", 0)
    if installs < prof.min_installs and owner not in trust.TRUSTED_PUBLISHERS:
        return f"{installs} installations (minimum {prof.min_installs})"
    sid = row["skill_id"]
    if not install.is_safe_name(sid):
        return "nom de dossier refusé"
    if sid.lower() in present:
        return "un skill porte déjà ce nom"
    if (row.get("md_name") or "").lower() != sid.lower():
        return "le nom déclaré dans SKILL.md diffère de celui de skills.sh"
    if not (row.get("description") or "").strip():
        return "SKILL.md sans description"
    if row.get("truncated", True):
        # Même chez un éditeur de confiance : la liste des fichiers est
        # incomplète, rien ne garantit que le dossier soit du texte pur.
        return "arborescence tronquée par GitHub"
    if row.get("executables") or row.get("exec_bits_in_scope") or row.get("opaque_in_scope"):
        return "fichier exécutable, lien symbolique ou sous-module"
    if row.get("content_hits"):
        return "motif sensible dans le SKILL.md : " + ", ".join(row["content_hits"])
    warnings = [f for f in row.get("flags", []) if f.startswith("⚠")]
    if warnings:
        return "drapeau de confiance : " + ", ".join(warnings)
    rel = install.relative_files(row)
    if rel is None:
        return "pas exactement un SKILL.md dans un dossier dédié"
    if len(rel) > install.MAX_FILES:
        return f"plus de {install.MAX_FILES} fichiers"
    bad = sorted(p for p in rel if not (install.is_text_file(p) and install.is_safe_relpath(p)))
    if bad:
        return f"fichier hors texte pur : {bad[0]}"
    return None


# Caractères que Claude lit mais qu'un humain ne voit pas (ou voit dans un
# autre ordre) : balises Unicode, sélecteurs de variante (VS1–VS15 et
# supplémentaires : ils suffisent à cacher un texte entier, 4 bits par
# caractère), contrôles de direction, sélecteurs mongols, remplisseurs
# Hangul, braille vide, liant graphème (CGJ), voyelles khmères invisibles.
# S'y ajoutent les catégories Cc (contrôles C0, DEL, C1 : ESC réécrit un
# terminal), Cf (format), Co (usage privé) et Cn (non attribué). Seuls
# restent admis la tabulation et les fins de ligne, le liant sans chasse
# (U+200D) et le sélecteur de présentation emoji (U+FE0F), qui composent les
# emoji, et l'indicateur d'ordre des octets en tête de fichier.
_HIDDEN_RANGES = ((0xE0000, 0xE007F), (0xE0100, 0xE01EF), (0xFE00, 0xFE0E),
                  (0x202A, 0x202E), (0x2066, 0x2069), (0x180B, 0x180F),
                  (0x115F, 0x1160), (0x3164, 0x3164), (0xFFA0, 0xFFA0),
                  (0x2800, 0x2800), (0x034F, 0x034F), (0x17B4, 0x17B5))
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Co", "Cn")
_HIDDEN_ALLOWED = {0x09, 0x0A, 0x0D, 0x200D, 0xFE0F}


def hidden_character(text: str) -> int | None:
    """Premier caractère invisible ou de contrôle de `text` (son code), ou None."""
    for i, ch in enumerate(text):
        cp = ord(ch)
        if (0x20 <= cp < 0x7F or cp in _HIDDEN_ALLOWED
                or (cp == 0xFEFF and i == 0)):
            continue
        if (any(lo <= cp <= hi for lo, hi in _HIDDEN_RANGES)
                or unicodedata.category(ch) in _HIDDEN_CATEGORIES):
            return cp
    return None


def load_files(row: dict, cache: github.Cache) -> str | None:
    """Octets exacts du dossier, scan déterministe de CHAQUE fichier (P4),
    texte complet pour Jev. Renvoie la raison d'un refus, ou None."""
    rel = install.relative_files(row)
    try:
        files = {r: github.fetch_blob_bytes(row["source"], sha, cache) for r, sha in rel.items()}
    except github.GhError as e:
        return f"fichier illisible : {e}"
    if sum(map(len, files.values())) > install.MAX_TOTAL_BYTES:
        return "dossier trop volumineux"
    texts = {}
    for r, data in files.items():
        try:
            texts[r] = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"{r} n'est pas du texte UTF-8"
        hidden = hidden_character(texts[r])
        if hidden is not None:
            return (f"caractères invisibles ou de contrôle de direction dans {r} "
                    f"(U+{hidden:04X})")
        execs, sensitive = trust.scan_skill_md(texts[r])
        if execs or sensitive:
            return f"motif relevé dans {r} : " + ", ".join(execs + sensitive)
    order = ["SKILL.md"] + sorted(r for r in texts if r != "SKILL.md")
    row["install_files"] = files
    row["install_expected"] = rel
    row["install_text"] = "\n\n".join(f"=== {r} ===\n{texts[r]}" for r in order)
    return None


def _priority(row: dict) -> tuple:
    r = row["jev"].relevance
    return (-max(r["meta"], r["stack"]), -r["substance"], -row.get("installs", 0),
            row["skill_id"])


def _summary(row: dict, path: str = "") -> dict:
    j = row.get("jev")
    return {"skill_id": row["skill_id"], "source": row["source"],
            "tree_sha": row.get("tree_sha", ""), "installs": row.get("installs", 0),
            "relevance": verdict.relevance_line(j, "install") if j else "",
            "description": row.get("description", ""), "path": path}


def _scores(j: verdict.Judgement) -> dict:
    return {**j.relevance, "severity": j.severity, "max_danger": max(j.dangers.values())}


def _inspect_all(cands: list[dict], cache: github.Cache, now: float,
                 result: RunResult) -> list[dict]:
    """Inspection parallèle. Toute erreur, prévue (GhError) ou non, écarte le
    seul candidat concerné. Si aucune inspection n'aboutit, GitHub est en
    panne (gh absent, non connecté…) : l'exécution passe en « source_error »."""
    def work(c):
        try:
            return inspection.inspect_candidate(c, cache, now)
        except github.GhError as e:
            return dict(c, inspect_error=str(e), inspect_reason=f"GitHub : {e}")
        except Exception as e:           # noqa: BLE001 — un candidat, pas l'exécution
            detail = f"{type(e).__name__} : {e}"
            return dict(c, inspect_error=detail, inspect_reason=f"inspection : {detail}")
    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        for r in pool.map(work, cands):
            if "inspect_error" in r:
                result.rejected.append((_label(r), r["inspect_reason"]))
                failures.append(r["inspect_error"])
            else:
                rows.append(r)
    if cands and not rows:
        result.status = "source_error"
        result.source_errors.append(f"GitHub : aucune inspection n'a abouti ({failures[0]})")
    return rows


def _screen(row: dict, prof: profile_mod.Profile, present: set[str],
            cache: github.Cache) -> str | None:
    """Critères avant Jev pour une ligne inspectée : raison d'un refus, ou
    None. Une exception écarte la ligne, jamais l'exécution."""
    try:
        return ((row["reason"] if row["excluded"] else None)
                or precheck(row, prof, present) or load_files(row, cache))
    except Exception as e:               # noqa: BLE001
        return f"erreur inattendue ({type(e).__name__} : {e})"


def _unique_names(eligible: list[dict], result: RunResult) -> list[dict]:
    """Un seul skill par nom de dossier : le mieux classé. Les suivants
    échoueraient à l'installation et prendraient une place du plafond."""
    kept: dict[str, dict] = {}
    for row in eligible:
        first = kept.setdefault(row["skill_id"].lower(), row)
        if first is not row:
            result.rejected.append((_label(row), "même nom qu'un skill mieux classé "
                                                 f"({first['source']})"))
    return list(kept.values())


def _upstream_changed(paths: config.Paths, cache: github.Cache, run_id: str) -> list[str]:
    """Skills installés lors d'exécutions précédentes dont le dossier amont a
    changé. Signalé seulement : la version installée reste celle jugée."""
    changed = []
    for e in install.installed(paths):
        if e["run_id"] == run_id:
            continue
        try:
            meta = github.fetch_repo(e["source"], cache)
            snap = github.fetch_tree_snapshot(e["source"], cache, meta.get("pushed_at", ""))
        except Exception:                # noqa: BLE001 — simple signalement, jamais bloquant
            continue
        if not snap.get("sha") or snap["sha"] == e["tree_sha"]:
            continue
        now_files = install.relative_files({
            "skill_md_paths": trust.locate_skill_mds(snap.get("paths", []), e["skill_id"]),
            "skill_files": snap.get("blobs", {})})
        if now_files != e["files"]:
            changed.append(f"{e['name']} ({e['source']})")
    return changed


def _run(paths: config.Paths, client, dry_run: bool, now: float, result: RunResult) -> None:
    try:
        prof = profile_mod.load_profile(paths)
    except profile_mod.ProfileError as e:
        result.status = "profile_error"
        result.errors.append(str(e))
        return
    if client is None or not client.available:
        result.status = "jev_unavailable"
        result.jev_status = ("TYPESAFE_API_KEY absente" if client is None
                             else client.last_error or "Jev indisponible")
        return
    try:
        present = install.present_names(paths)
    except install.InstallError as e:
        result.status = "install_error"
        result.errors.append(str(e))
        return

    cands, result.source_errors, search_down = discover(prof)
    if search_down and not cands:
        result.status = "source_error"
        result.source_errors.append("skills.sh : aucune recherche n'a abouti "
                                    f"({result.source_errors[0]})")
        return
    cands = [c for c in cands if sources.is_github_source(c["source"])
             and c["skill_id"].lower() not in present][:prof.max_candidates]
    result.candidates = len(cands)
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    cache = github.Cache(str(paths.cache_db))

    survivors = []
    inspected = _inspect_all(cands, cache, now, result)
    if result.status == "source_error":
        return
    for row in inspected:
        reason = _screen(row, prof, present, cache)
        if reason:
            result.rejected.append((_label(row), reason))
        else:
            survivors.append(row)

    verdict.judge_rows(survivors, client, "install", profile=prof.jev_text(), cache=cache,
                       text_of=lambda r: r["install_text"])
    result.jev_calls = client.calls
    if client.key_rejected:
        # Clé refusée en cours de route : même les jugements « ok » tirés du
        # cache n'installent rien (D4, fermeture en échec).
        result.status = "jev_unavailable"
        result.jev_status = client.last_error or "clé TYPESAFE_API_KEY refusée"
        return
    if not client.available:
        result.jev_status = client.last_error or "Jev indisponible"

    eligible = []
    for row in survivors:
        reason = verdict.install_verdict(row["jev"])
        if reason:
            result.rejected.append((_label(row), reason))
        else:
            eligible.append(row)
    eligible.sort(key=_priority)
    eligible = _unique_names(eligible, result)
    result.pending = [_summary(r) for r in eligible[prof.max_installs:]]
    for row in eligible[:prof.max_installs]:
        target = paths.skills_dir / row["skill_id"]
        if not dry_run:
            try:
                target = install.install_skill(
                    row, row["install_files"], row["install_expected"], paths,
                    run_id=result.run_id, scores=_scores(row["jev"]), now=result.run_id)
            except install.InstallError as e:
                result.errors.append(str(e))
                continue
        result.installed.append(_summary(row, str(target)))
    result.upstream_changed = _upstream_changed(paths, cache, result.run_id)


def _write_journal_safe(paths: config.Paths, result: RunResult) -> None:
    """Le journal est la dernière trace disponible : s'il ne peut pas être
    écrit non plus (disque plein, permission refusée, fichier brièvement
    verrouillé sous pythonw), il n'y a plus rien à faire de plus que de ne
    pas planter une tâche sans surveillance. L'état de l'exécution reste
    lisible via le code de sortie / le résumé de la CLI."""
    try:
        report.append_journal(paths, result)
    except OSError:
        pass


def _record(paths: config.Paths, result: RunResult) -> None:
    """Écrit le rapport puis le journal, chacun protégé contre une panne
    d'écriture : `run_routine` ne doit jamais lever pour ça. Le rapport est
    tenté avant le journal pour que l'échec du rapport, s'il y en a un,
    apparaisse dans la ligne de journal."""
    try:
        report.write_report(paths, result)
    except OSError as e:
        result.errors.append(f"rapport non écrit : {e}")
    _write_journal_safe(paths, result)


def run_routine(paths: config.Paths, *, client, dry_run: bool = False,
                now: float | None = None) -> RunResult:
    """Une exécution complète. Ne lève pas : tâche sans surveillance, toute
    erreur finit dans le rapport et le journal. Rapport et journal sont
    écrits pendant que le verrou est encore tenu, pour qu'une exécution
    concurrente ne puisse jamais entrelacer ses écritures avec celles du
    même rapport hebdomadaire."""
    now = time.time() if now is None else now
    result = RunResult(run_id=run_id_of(now), dry_run=dry_run)
    if not acquire_lock(paths.lock_file):
        result.status = "locked"
        _write_journal_safe(paths, result)
        return result
    try:
        try:
            _run(paths, client, dry_run, now, result)
        except Exception as e:       # noqa: BLE001 — l'erreur va au rapport
            result.status = "error"
            result.errors.append(f"{type(e).__name__} : {e}")
        _record(paths, result)
    finally:
        release_lock(paths.lock_file)
    return result
