"""Routine hebdomadaire : découverte, critères stricts, Jev, installation
plafonnée (spec § Routine). Fermeture en échec : dans le doute, on n'installe pas."""
from __future__ import annotations

import datetime as _dt
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import profile as profile_mod, sources

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


def discover(prof: profile_mod.Profile) -> tuple[list[dict], list[str]]:
    """Requêtes thématiques (source principale) puis classements (complément).
    Une source en panne est notée, jamais bloquante."""
    found: dict[tuple[str, str], dict] = {}
    errors: list[str] = []

    def add(c: dict) -> None:
        key = (c["source"].lower(), c["skill_id"].lower())
        if key not in found or c["installs"] > found[key]["installs"]:
            found[key] = c
    for theme in prof.meta_themes + prof.stack_themes:
        try:
            for c in sources.search_skills(theme, limit=prof.per_query):
                add(c)
        except sources.SearchError as e:
            errors.append(f"recherche « {theme} » : {e}")
    if prof.leaderboard_top:
        for kind in sources.LEADERBOARDS:
            try:
                for c in sources.fetch_leaderboard(kind, top=prof.leaderboard_top):
                    add(c)
            except sources.LeaderboardError as e:
                errors.append(f"classement {kind} : {e}")
    return sorted(found.values(), key=lambda c: -c["installs"]), errors
