"""Constantes partagées par plusieurs modules."""
from __future__ import annotations

import os

CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "skillscout", "cache.db")
MAX_LIMIT = 100          # skills.sh ne renvoie jamais plus ; borne les appels GitHub
WORKERS = 6              # dépôts inspectés en parallèle
SKILL_MD_SCAN_LIMIT = 500_000   # caractères analysés ; au-delà, non vérifiable
