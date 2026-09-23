"""Recherche de skills sur skills.sh."""
from __future__ import annotations

import re
from urllib.parse import quote

from . import net

SEARCH_URL = "https://skills.sh/api/search?q={}"

# Forme stricte d'un identifiant GitHub `owner/repo`. `source` vient de
# skills.sh, pas de nous : rien n'est interpolé dans `gh api …` sans passer
# ce filtre. Les sources non GitHub (smithery.ai…) sont ignorées et signalées.
GITHUB_SOURCE_RE = re.compile(
    r"^(?!\.+/)[A-Za-z0-9_.-]{1,100}/(?!\.+$)[A-Za-z0-9_.-]{1,100}$")  # ni `.` ni `..`


class SearchError(Exception):
    """skills.sh est injoignable ou a répondu autre chose que du JSON."""


def search_skills(query: str, limit: int = 25) -> list[dict]:
    """Interroge skills.sh et renvoie les `limit` candidats les plus installés.

    skills.sh renvoie ses résultats triés par pertinence ; `relevance_rank`
    capture la position de chaque candidat dans CET ordre (0 = premier
    résultat de l'API), avant le retri par installations ci-dessous. La
    pertinence ne sert qu'à départager des scores égaux plus loin dans le
    pipeline (voir `rank()`), jamais à choisir qui est tronqué ici.
    """
    try:
        data = net.get_json(SEARCH_URL.format(quote(query)))
    except (OSError, ValueError) as e:  # URLError ⊂ OSError ; JSON invalide ⊂ ValueError
        raise SearchError(f"skills.sh injoignable ou illisible : {e}") from e
    out = [
        {
            "skill_id": s.get("skillId") or s.get("name") or "",
            "name": s.get("name") or "",
            "source": s.get("source") or "",
            "installs": int(s.get("installs") or 0),
            "relevance_rank": rank,
        }
        for rank, s in enumerate(data.get("skills", []))
        if s.get("source")
    ]
    out.sort(key=lambda s: s["installs"], reverse=True)
    return out[:limit]


def is_github_source(source: str) -> bool:
    return bool(GITHUB_SOURCE_RE.match(source or ""))
