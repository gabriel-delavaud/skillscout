"""Installation automatique : noms sûrs, écriture atomique des octets jugés,
manifeste, désinstallation. N'écrit jamais dans le registre de `npx skills`
(D6), n'écrase jamais rien, ne supprime que ce que skillscout a installé."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path

from . import config, github

MANIFEST_VERSION = 1
MAX_FILES = 50
MAX_TOTAL_BYTES = 1_000_000
TEXT_SUFFIXES = (".md", ".txt")
TEXT_BASENAMES = ("license", "notice")

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class InstallError(Exception):
    """Installation ou désinstallation refusée ; rien n'a été laissé à moitié."""


def _reserved(segment: str) -> bool:
    return segment.split(".")[0].lower() in _WINDOWS_RESERVED or segment.endswith(".")


def is_safe_name(name: str) -> bool:
    """Nom de dossier sous ~/.claude/skills. `skill_id` vient de skills.sh,
    pas de nous : rien n'est écrit sans passer ce filtre."""
    return bool(_SAFE_NAME.match(name)) and not _reserved(name)


def is_safe_relpath(rel: str) -> bool:
    parts = rel.split("/")
    return bool(rel) and all(_SAFE_SEGMENT.match(s) and not _reserved(s) for s in parts)


def is_text_file(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1].lower()
    return base.endswith(TEXT_SUFFIXES) or base in TEXT_BASENAMES


def relative_files(row: dict) -> dict[str, str] | None:
    mds = row.get("skill_md_paths") or []
    if len(mds) != 1 or "/" not in mds[0]:
        return None
    prefix = mds[0].rsplit("/", 1)[0] + "/"
    return {p[len(prefix):]: sha for p, sha in (row.get("skill_files") or {}).items()
            if p.startswith(prefix)}


def read_manifest(paths: config.Paths) -> dict:
    try:
        data = json.loads(paths.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": MANIFEST_VERSION, "skills": []}
    except (OSError, ValueError) as e:
        raise InstallError(f"manifeste illisible ({paths.manifest}) : {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise InstallError(f"manifeste invalide ({paths.manifest})")
    return data


def installed(paths: config.Paths) -> list[dict]:
    return read_manifest(paths)["skills"]


def _write_manifest(paths: config.Paths, data: dict) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    tmp = paths.manifest.with_name(paths.manifest.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, paths.manifest)


def present_names(paths: config.Paths) -> set[str]:
    """Noms déjà pris : dossiers de ~/.claude/skills, registre de `npx skills`,
    manifeste de skillscout. En minuscules (Windows ignore la casse)."""
    names: set[str] = set()
    if paths.skills_dir.is_dir():
        names |= {p.name.lower() for p in paths.skills_dir.iterdir()}
    try:
        lock = json.loads(paths.skill_lock.read_text(encoding="utf-8"))
        names |= {k.lower() for k in (lock.get("skills") or {})}
    except (OSError, ValueError, AttributeError):
        pass   # registre absent ou illisible : les dossiers suffisent à éviter l'écrasement
    names |= {e["name"].lower() for e in installed(paths)}
    return names


def _check(name: str, files: dict[str, bytes], expected: dict[str, str]) -> None:
    if not is_safe_name(name):
        raise InstallError(f"nom de skill refusé : {name!r}")
    if set(files) != set(expected) or "SKILL.md" not in files:
        raise InstallError(f"{name} : fichiers incomplets")
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise InstallError(f"{name} : trop de fichiers ou trop volumineux")
    # Deux chemins qui ne diffèrent que par la casse partagent un seul fichier
    # sur un disque insensible à la casse (Windows) : le contenu réellement
    # posé ne correspondrait alors plus jamais à ce que le manifeste a promis
    # pour l'un des deux, et la désinstallation serait bloquée pour toujours.
    lowered_paths: dict[str, str] = {}
    lowered_dirs: dict[str, str] = {}
    for rel, data in files.items():
        if not is_safe_relpath(rel) or not is_text_file(rel):
            raise InstallError(f"{name} : fichier refusé {rel!r}")
        if github.git_blob_sha(data) != expected[rel]:
            raise InstallError(f"{name} : {rel} ne correspond pas à l'empreinte jugée")
        key = rel.lower()
        if lowered_paths.setdefault(key, rel) != rel:
            raise InstallError(f"{name} : {rel!r} et {lowered_paths[key]!r} ne diffèrent que "
                               "par la casse")
        prefix = ""
        for segment in rel.split("/")[:-1]:
            prefix = f"{prefix}/{segment}" if prefix else segment
            pkey = prefix.lower()
            if lowered_dirs.setdefault(pkey, prefix) != prefix:
                raise InstallError(f"{name} : dossiers {prefix!r} et {lowered_dirs[pkey]!r} ne "
                                   "diffèrent que par la casse")


def install_skill(row: dict, files: dict[str, bytes], expected: dict[str, str],
                  paths: config.Paths, *, run_id: str, scores: dict, now: str) -> Path:
    """Écrit `files` (chemin relatif → octets) dans ~/.claude/skills/<skill_id>.
    Préparé hors du dossier des skills puis renommé d'un coup : Claude ne voit
    jamais un skill à moitié écrit. Lève InstallError sans rien laisser."""
    name = row["skill_id"]
    _check(name, files, expected)
    target = paths.skills_dir / name
    if target.exists() or name.lower() in present_names(paths):
        raise InstallError(f"{name} : un skill porte déjà ce nom, rien n'est écrasé")
    # L'entrée de manifeste et sa forme JSON sont construites et validées AVANT
    # de toucher skills_dir : une ligne mal formée (source manquante, scores non
    # sérialisables) doit échouer pendant que rien n'existe encore sur le disque,
    # pas après le renommage qui rend le skill visible.
    try:
        entry = {"name": name, "source": row["source"], "skill_id": row["skill_id"],
                 "tree_sha": row.get("tree_sha", ""), "files": dict(expected),
                 "installed_at": now, "run_id": run_id, "scores": scores}
        manifest_data = read_manifest(paths)
        manifest_data["skills"].append(entry)
        json.dumps(manifest_data, ensure_ascii=False, indent=2)  # valide tôt, jeté ensuite
    except InstallError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise InstallError(f"{name} : entrée de manifeste invalide ({e})") from e
    paths.staging_dir.mkdir(parents=True, exist_ok=True)
    stage = paths.staging_dir / f"{name}-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        for rel, data in files.items():
            dest = stage.joinpath(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for rel, sha in expected.items():          # relecture : le disque dit la même chose
            if github.git_blob_sha(stage.joinpath(*rel.split("/")).read_bytes()) != sha:
                raise InstallError(f"{name} : relecture différente pour {rel}")
        paths.skills_dir.mkdir(parents=True, exist_ok=True)
        os.rename(stage, target)                   # échoue si la cible existe
    except InstallError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise InstallError(f"{name} : écriture impossible ({e})") from e
    # À partir d'ici, le dossier existe dans skills_dir : toute panne, quelle
    # qu'elle soit, doit le retirer plutôt que laisser un skill installé sans
    # entrée de manifeste (impossible à désinstaller, et bloquant une
    # réinstallation ultérieure sous le même nom).
    try:
        _write_manifest(paths, manifest_data)
    except BaseException as e:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(e, Exception):
            raise InstallError(f"{name} : manifeste non écrit, installation annulée ({e})") from e
        raise
    return target


def _is_plain_dir(p: Path) -> bool:
    is_junction = getattr(p, "is_junction", lambda: False)()
    return p.is_dir() and not p.is_symlink() and not is_junction


def _tree_shas(root: Path) -> dict[str, str] | None:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            if not _is_plain_dir(Path(dirpath) / d):
                return None
        for f in filenames:
            p = Path(dirpath) / f
            if p.is_symlink():
                return None
            out[p.relative_to(root).as_posix()] = github.git_blob_sha(p.read_bytes())
    return out


def _clear_readonly_and_retry(func, path, exc_info):
    """Gestionnaire d'erreur pour `shutil.rmtree` : un fichier posé en lecture
    seule (courant pour un clone de dépôt) ne doit pas laisser un reste dans
    le dossier de préparation. Best effort : si ça échoue encore, le reste
    demeure hors de skills_dir, ce qui ne met plus rien en danger."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def uninstall(name: str, paths: config.Paths) -> Path:
    """Retire un skill installé par skillscout. Refuse tout dossier absent du
    manifeste, ou modifié depuis l'installation. Le dossier est d'abord
    déplacé d'un bloc hors de skills_dir (le symétrique de l'installation) :
    à aucun moment il n'est ni à moitié supprimé ni à moitié présent, et si
    le déplacement échoue, le skill et le manifeste restent intacts."""
    data = read_manifest(paths)
    entry = next((e for e in data["skills"] if e["name"].lower() == name.lower()), None)
    if entry is None:
        raise InstallError(f"{name} n'a pas été installé par skillscout : rien n'est supprimé")
    target = paths.skills_dir / entry["name"]
    moved = None
    if target.exists() or target.is_symlink():
        try:
            unchanged = _is_plain_dir(target) and _tree_shas(target) == entry["files"]
        except OSError as e:
            raise InstallError(f"{target} illisible, désinstallation refusée ({e})") from e
        if not unchanged:
            raise InstallError(f"{target} a changé depuis son installation : supprimez-le "
                               "vous-même si c'est voulu")
        paths.staging_dir.mkdir(parents=True, exist_ok=True)
        moved = paths.staging_dir / f"{entry['name']}-removed-{os.getpid()}"
        shutil.rmtree(moved, ignore_errors=True)
        try:
            os.rename(target, moved)                # même volume : atomique, ou échoue net
        except OSError as e:
            raise InstallError(f"{target} : retrait impossible ({e})") from e
    data["skills"] = [e for e in data["skills"] if e is not entry]
    try:
        _write_manifest(paths, data)
    except OSError as e:
        if moved is not None:
            try:                                     # on remet le dossier en place : le
                os.rename(moved, target)              # manifeste au repos le dit encore installé
            except OSError:
                pass
        raise InstallError(f"{target} : manifeste non mis à jour, désinstallation annulée "
                           f"({e})") from e
    if moved is not None:
        shutil.rmtree(moved, onerror=_clear_readonly_and_retry)
    return target


def uninstall_last(paths: config.Paths) -> tuple[list[Path], list[str]]:
    """Retire le lot de la dernière exécution de la routine."""
    entries = installed(paths)
    if not entries:
        return [], []
    last = max(e["run_id"] for e in entries)
    removed, errors = [], []
    for e in [e for e in entries if e["run_id"] == last]:
        try:
            removed.append(uninstall(e["name"], paths))
        except InstallError as err:
            errors.append(str(err))
    return removed, errors
