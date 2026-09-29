"""Constantes partagées par plusieurs modules."""
from __future__ import annotations

import os
from pathlib import Path

CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "skillscout", "cache.db")
MAX_LIMIT = 100          # skills.sh ne renvoie jamais plus ; borne les appels GitHub
SEARCH_BATCH = 25        # candidats inspectés (et jugés par Jev) par lot
SEARCH_CEILING = 75      # plafond par défaut de la recherche manuelle, tous lots confondus
TOP_N = 10               # skills montrables recherchés ; les lots s'arrêtent dès qu'il y en a assez
DISPLAY_N = 5            # skills affichés : les meilleurs des TOP_N trouvés
JEV_OUTAGE_CALLS = 3     # appels à Jev en échec d'affilée, après un succès : panne, arrêt
WORKERS = 6              # dépôts inspectés en parallèle
SKILL_MD_SCAN_LIMIT = 500_000   # caractères analysés ; au-delà, non vérifiable


def skills_dir() -> Path:
    """Dossier des skills de Claude Code, lu (jamais écrit) pour « déjà installé »."""
    return Path.home() / ".claude" / "skills"
