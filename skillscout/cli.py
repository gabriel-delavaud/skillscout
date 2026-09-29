"""Interface en ligne de commande."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from . import config, github, inspection, install, jev, rank, report, routine, schedule, sources, verdict


def format_top10(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        sha = (r.get("tree_sha") or "")[:7]
        where = f"{r['source']}@{sha}" if sha else r["source"]
        lines.append(
            f"{i:2}. {r['skill_id']:<34} {r['score']:>6.1f}  "
            f"{where:<40} {' · '.join(r.get('flags', []))}"
        )
        extra = r.get("relevance")
        if extra:
            desc = " ".join((r.get("description") or "").split())
            if len(desc) > 90:
                desc = desc[:89] + "…"
            lines.append(f"    {extra}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def format_excluded(rows: list[dict]) -> str:
    return "\n".join(f" - {r['skill_id']:<34} {r['source']:<32} {r['reason']}"
                     for r in rows)


_HINT = ('Astuce : décris le besoin avec des mots courts du domaine, par exemple\n'
         '  skillscout "{need}" -q "eval harness" -q "llm judge"')


def _inspect_batch(batch: list[dict], cache, now: float, progress: bool
                   ) -> tuple[list[dict], list[dict], list[dict]]:
    """Inspection GitHub d'un lot. Renvoie (évalués, ignorés sur erreur gh,
    ignorés car non GitHub)."""
    # Nommé `gh_candidates`, et non `github`, pour ne pas masquer le module.
    gh_candidates, skipped = [], []
    for c in batch:
        (gh_candidates if sources.is_github_source(c["source"]) else skipped).append(c)
    for c in skipped:
        print(f"  ignoré {c['skill_id']} : source non GitHub ({c['source']})",
              file=sys.stderr)

    def worker(c):
        try:
            return inspection.inspect_candidate(c, cache, now)
        except github.GhError as e:
            return dict(c, gh_error=str(e))

    evaluated, ignored = [], []
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        for i, row in enumerate(pool.map(worker, gh_candidates), 1):
            if progress:
                print(f"\r  {i}/{len(gh_candidates)} dépôts inspectés", end="",
                      file=sys.stderr, flush=True)
            (ignored if "gh_error" in row else evaluated).append(row)
    if progress:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)
    for r in ignored:
        print(f"  ignoré {r['source']} : {r['gh_error']}", file=sys.stderr)
    return evaluated, ignored, skipped


def _mark_local(rows: list[dict]) -> None:
    """Drapeaux « déjà installé », « +N copies » et « +N variantes » des lignes affichées."""
    skills_dir = config.default_paths().skills_dir
    for r in rows:
        status = install.local_status(r, skills_dir)
        via = None
        if status == "other":     # l'installé est peut-être une autre version du groupe
            via = next((m["source"] for m in r.get("_members", ())
                        if not m["excluded"] and install.local_status(m, skills_dir) == "same"),
                       None)
            if via:
                status = "variant"
        r["installed"] = status
        flags = list(r.get("flags", []))
        if status == "same":
            flags.insert(0, "✓ déjà installé")
        elif status == "variant":
            flags.insert(0, f"✓ variante installée ({via})")
        elif status == "other":
            flags.insert(0, "≈ autre version installée")
        if r.get("copies"):
            flags.append(f"+{len(r['copies'])} copie(s)")
        if r.get("variants"):
            flags.append(f"+{len(r['variants'])} variante(s)")
        if r.get("excluded_versions"):
            flags.append(f"{len(r['excluded_versions'])} autre(s) version(s) écartée(s)")
        r["flags"] = flags


def _main_search(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout",
        description="Cherche sur skills.sh les skills qui répondent à un besoin ; "
                    "Jev juge leur pertinence et leur sûreté.",
        epilog="Autres commandes : skillscout routine | uninstall | installed "
               "(--help pour chacune).")
    ap.add_argument("besoin", help="ce que le skill doit savoir faire ; Jev juge la "
                                   "pertinence par rapport à cette phrase")
    ap.add_argument("-q", "--query", action="append", metavar="REQUÊTE",
                    help="requête envoyée à skills.sh, répétable (mots courts du domaine : "
                         "« eval harness »). Par défaut : le besoin lui-même")
    ap.add_argument("--json", action="store_true",
                    help="sortie machine du top 10")
    ap.add_argument("--show-excluded", action="store_true",
                    help="liste aussi les candidats écartés et pourquoi")
    ap.add_argument("--limit", type=int, default=config.SEARCH_CEILING,
                    help=f"plafond de candidats examinés, 1 à {config.MAX_LIMIT}, par lots "
                         f"de {config.SEARCH_BATCH} (borne les appels GitHub et Jev)")
    ap.add_argument("--no-jev", action="store_true",
                    help="classement déterministe seul, sans envoyer les SKILL.md à Jev")
    args = ap.parse_args(argv)
    if not 1 <= args.limit <= config.MAX_LIMIT:
        ap.error(f"--limit doit être entre 1 et {config.MAX_LIMIT}")
    queries = [q.strip() for q in (args.query or [args.besoin]) if q.strip()]
    if not queries:
        ap.error("requête vide")

    os.makedirs(os.path.dirname(config.CACHE_PATH), exist_ok=True)
    cache = github.Cache(config.CACHE_PATH)
    now = time.time()

    try:
        candidates, failures = sources.search_many(queries)
    except sources.SearchError as e:
        print(str(e), file=sys.stderr)
        return 1
    for f in failures:
        print(f"  recherche en échec {f}", file=sys.stderr)
    if not candidates:
        print("Aucun candidat sur skills.sh pour cette recherche.", file=sys.stderr)
        return 1

    client, banner = None, ""
    if not args.no_jev:
        client = jev.JevClient.from_env()
        if client is None:
            banner = "Jev indisponible : TYPESAFE_API_KEY absente — classement déterministe seul."
    use_jev, jev_ok = client is not None, False
    progress = sys.stderr.isatty()

    # Lots successifs, dans l'ordre de pertinence de skills.sh, jusqu'à avoir
    # TOP_N skills à montrer (pertinents selon Jev, ou simplement non écartés
    # sans Jev) ou atteindre le plafond.
    rows: list[dict] = []
    ignored, skipped, examined, pos, batch_no = [], [], 0, 0, 0
    ceiling = min(args.limit, len(candidates))
    while pos < ceiling:
        batch = candidates[pos:min(pos + config.SEARCH_BATCH, ceiling)]
        pos += len(batch)
        batch_no += 1
        evaluated, ign, skp = _inspect_batch(batch, cache, now, progress)
        ignored += ign
        skipped += skp
        examined += len(evaluated)
        todo = rank.add_deduplicated(rows, evaluated)
        outage = False
        while use_jev and todo:
            verdict.judge_rows(todo, client, "manual", need=args.besoin)
            judged = [r for r in todo if "jev" in r]
            if judged and not any(r["jev"].status == "ok" for r in judged):
                note = judged[0]["jev"].note
                if not jev_ok:        # Jev n'a encore jamais répondu : repli déterministe
                    banner = f"Jev indisponible ({note}) — classement déterministe seul."
                    for r in rank.members(rows):
                        r.pop("jev", None)
                    use_jev = False
                else:                 # panne en cours de route : on s'arrête là
                    banner = (f"Jev indisponible à partir du lot {batch_no} ({note}) — "
                              "résultats partiels.")
                    outage = True
                break
            jev_ok = jev_ok or bool(judged)
            verdict.apply_manual(todo)
            # Une version rejetée par Jev laisse sa place à la suivante du groupe.
            todo = rank.promote(rows)
        if outage:
            break
        shown = [r for r in rows if (rank.is_relevant(r) if use_jev else not r["excluded"])]
        if len(shown) >= config.TOP_N:
            break
    if banner:
        print(banner, file=sys.stderr)

    if use_jev:
        for r in rows:
            if "jev" in r:
                r["relevance"] = verdict.relevance_line(r["jev"], "manual")
    excluded_rows = [r for r in rank.members(rows) if r["excluded"]]
    unjudged = sum(1 for r in rows if use_jev and not r["excluded"]
                   and "jev" in r and r["jev"].status != "ok")
    top = (rank.rank_with_jev if use_jev else rank.rank)(rows, top=config.TOP_N)
    hint = use_jev and not args.query and len(top) < 3
    if not top:
        if examined == 0:
            print(f"Aucun candidat n'a pu être inspecté ({len(ignored) + len(skipped)} "
                  "ignoré(s)).", file=sys.stderr)
        elif use_jev and not all(r["excluded"] for r in rows):
            print(f"Aucun skill pertinent parmi les {examined} candidat(s) examiné(s)"
                  + (f" ({unjudged} non jugé(s) par Jev)." if unjudged else "."),
                  file=sys.stderr)
        else:
            print(f"Les {examined} candidat(s) examiné(s) ont tous été écartés.",
                  file=sys.stderr)
        if hint:
            print(_HINT.format(need=args.besoin), file=sys.stderr)
        if args.show_excluded and excluded_rows:
            print(format_excluded(excluded_rows), file=sys.stderr)
        return 1
    for r in top:
        rank.describe_group(r)
    _mark_local(top)

    if args.json:
        hide = {"body", "paths", "skill_files", "_members", "_tries"}
        print(json.dumps([{k: (asdict(v) if k == "jev" else v)
                           for k, v in r.items() if k not in hide}
                          for r in top], ensure_ascii=False, indent=2))
        return 0

    counts = (f"{len(excluded_rows)} écarté(s) sur {examined} examiné(s), "
              f"{len(ignored) + len(skipped)} ignoré(s)"
              + (f", {unjudged} non jugé(s) par Jev" if unjudged else ""))
    if use_jev and len(top) < config.TOP_N:
        print(f"\nSeulement {len(top)} skill(s) pertinent(s) trouvé(s) ({counts})\n")
    elif use_jev:
        print(f"\nTOP {config.TOP_N} par pertinence (Jev) ({counts})\n")
    else:
        print(f"\nTOP {config.TOP_N} par pertinence skills.sh ({counts})\n")
    print(format_top10(top))
    print("\nLe @sha après le dépôt identifie l'arborescence évaluée ; le "
          "SKILL.md analysé est celui de cet instantané.")
    if hint:
        print("\n" + _HINT.format(need=args.besoin))

    if args.show_excluded and excluded_rows:
        print(f"\nÉcartés ({len(excluded_rows)}) :\n")
        print(format_excluded(excluded_rows))

    return 0


def _main_routine(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout routine",
        description="Découvre, juge (Jev) et installe au plus quelques skills ; "
                    "réglages dans ~/.config/skillscout/profile.toml.")
    ap.add_argument("--dry-run", action="store_true",
                    help="tout, sauf l'écriture dans ~/.claude/skills")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--register", action="store_true",
                   help="crée la tâche Windows hebdomadaire (lundi 10 h)")
    g.add_argument("--unregister", action="store_true", help="supprime la tâche Windows")
    args = ap.parse_args(argv)
    if args.register:
        return schedule.register()
    if args.unregister:
        return schedule.unregister()
    paths = config.default_paths()
    res = routine.run_routine(paths, client=jev.JevClient.from_env(), dry_run=args.dry_run)
    label = report.STATUS_LABELS.get(res.status, res.status)
    rest = f"{len(res.pending)} en attente, {len(res.rejected)} écarté(s)"
    if args.dry_run:                  # rien n'a été écrit : ne pas annoncer d'installation
        print(f"Simulation : {len(res.installed)} à installer, {rest} (routine {label}).")
    else:
        print(f"Routine {label} : {len(res.installed)} installé(s), {rest}.")
    report_path = paths.reports_dir / f"{report.iso_week_name(res.run_id)}.md"
    if report_path.exists():
        print(f"Rapport : {report_path}")
    return 0 if res.status == "ok" else 1


def _main_uninstall(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout uninstall",
        description="Retire un skill installé par skillscout, et seulement ceux-là.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("nom", nargs="?", help="nom du skill (voir skillscout installed)")
    g.add_argument("--last", action="store_true", help="retire le lot de la dernière routine")
    args = ap.parse_args(argv)
    paths = config.default_paths()
    try:
        if not args.last:
            print(f"retiré : {install.uninstall(args.nom, paths)}")
            return 0
        removed, errors = install.uninstall_last(paths)
    except install.InstallError as e:
        print(str(e), file=sys.stderr)
        return 1
    for p in removed:
        print(f"retiré : {p}")
    for e in errors:
        print(e, file=sys.stderr)
    if not removed and not errors:
        print("Rien à retirer : skillscout n'a encore rien installé.")
    return 1 if errors else 0


def _main_installed(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="skillscout installed",
                            description="Skills installés par skillscout.").parse_args(argv)
    try:
        entries = install.installed(config.default_paths())
    except install.InstallError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not entries:
        print("Aucun skill installé par skillscout.")
        return 0
    for e in sorted(entries, key=lambda e: e["installed_at"]):
        print(f"{e['name']:<32} {e['source']}@{e['tree_sha'][:7]}  "
              f"{e['installed_at']}  lot {e['run_id']}")
    return 0


_SUBCOMMANDS = {"routine": _main_routine, "uninstall": _main_uninstall,
                "installed": _main_installed}


def main(argv: list[str]) -> int:
    """`skillscout "besoin"` (ou `skillscout search "besoin"`) cherche ;
    les autres sous-commandes sont reconnues par leur premier mot."""
    if argv and argv[0] in _SUBCOMMANDS:
        return _SUBCOMMANDS[argv[0]](argv[1:])
    if argv and argv[0] == "search":
        argv = argv[1:]
    return _main_search(argv)


def cli() -> None:
    """Point d'entrée `skillscout` installé par pip/pipx/uv."""
    raise SystemExit(main(sys.argv[1:]))
