"""Routine hebdomadaire : découverte, critères stricts, Jev, installation
plafonnée (spec § Routine). Fermeture en échec : dans le doute, on n'installe pas."""
from __future__ import annotations

import datetime as _dt
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import profile as profile_mod, sources

LOCK_STALE_S = 6 * 3600      # un verrou plus vieux vient d'une exécution tuée


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


def acquire_lock(path: Path, now: float | None = None) -> bool:
    """Crée le verrou de façon exclusive. Un verrou orphelin (plus vieux que
    LOCK_STALE_S) est repris une fois."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = (time.time() if now is None else now) - path.stat().st_mtime
            except OSError:
                return False
            if age < LOCK_STALE_S:
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def release_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


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
