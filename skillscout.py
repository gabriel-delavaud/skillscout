"""skillscout — trie les skills de skills.sh par confiance, puis fait expliquer
un top 3 par un LLM local. Bibliothèque standard uniquement."""

import json
from urllib.parse import quote
from urllib.request import urlopen, Request

SEARCH_URL = "https://skills.sh/api/search?q={}"
HTTP_TIMEOUT = 20


def _get_json(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "skillscout"})
    with urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def search_skills(query: str, limit: int = 25) -> list[dict]:
    """Interroge skills.sh et renvoie les `limit` candidats les plus installés."""
    data = _get_json(SEARCH_URL.format(quote(query)))
    out = [
        {
            "skill_id": s.get("skillId") or s.get("name") or "",
            "name": s.get("name") or "",
            "source": s.get("source") or "",
            "installs": int(s.get("installs") or 0),
        }
        for s in data.get("skills", [])
        if s.get("source")
    ]
    out.sort(key=lambda s: s["installs"], reverse=True)
    return out[:limit]
