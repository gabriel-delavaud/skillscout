"""Constantes partagées par plusieurs modules."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "skillscout", "cache.db")
MAX_LIMIT = 100          # skills.sh ne renvoie jamais plus ; borne les appels GitHub
SEARCH_BATCH = 25        # candidats inspectés (et jugés par Jev) par lot
SEARCH_CEILING = 75      # plafond par défaut de la recherche manuelle, tous lots confondus
TOP_N = 10               # skills affichés ; les lots s'arrêtent dès qu'il y en a assez
JEV_OUTAGE_CALLS = 3     # appels à Jev en échec d'affilée, après un succès : panne, arrêt
WORKERS = 6              # dépôts inspectés en parallèle
SKILL_MD_SCAN_LIMIT = 500_000   # caractères analysés ; au-delà, non vérifiable


@dataclass(frozen=True)
class Paths:
    """Tous les emplacements écrits par la routine. Les tests en construisent
    un sous un dossier temporaire : aucun ne touche le vrai profil."""
    cache_dir: Path
    config_dir: Path
    claude_dir: Path
    skill_lock: Path

    @property
    def skills_dir(self) -> Path:
        return self.claude_dir / "skills"

    @property
    def staging_dir(self) -> Path:
        # Hors de skills/ (Claude n'y voit pas de skill à moitié écrit), même
        # volume (le renommage final est atomique).
        return self.claude_dir / ".skillscout-staging"

    @property
    def cache_db(self) -> Path:
        return self.cache_dir / "cache.db"

    @property
    def reports_dir(self) -> Path:
        return self.cache_dir / "reports"

    @property
    def journal(self) -> Path:
        return self.cache_dir / "journal.jsonl"

    @property
    def lock_file(self) -> Path:
        return self.cache_dir / "routine.lock"

    @property
    def manifest(self) -> Path:
        return self.config_dir / "installed.json"

    @property
    def profile(self) -> Path:
        return self.config_dir / "profile.toml"

    @classmethod
    def under(cls, root: Path) -> "Paths":
        return cls(root / "cache", root / "config", root / "claude",
                   root / "agents" / ".skill-lock.json")


def default_paths() -> Paths:
    home = Path.home()
    return Paths(cache_dir=Path(CACHE_PATH).parent,
                 config_dir=home / ".config" / "skillscout",
                 claude_dir=home / ".claude",
                 skill_lock=home / ".agents" / ".skill-lock.json")
