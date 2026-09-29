"""Recherche de skills sur skills.sh."""
from __future__ import annotations

import re
from urllib.parse import quote

from . import config, net

SEARCH_URL = "https://skills.sh/api/search?q={}"

# Forme stricte d'un identifiant GitHub `owner/repo`. `source` vient de
# skills.sh, pas de nous : rien n'est interpolé dans `gh api …` sans passer
# ce filtre. Les sources non GitHub (smithery.ai…) sont ignorées et signalées.
GITHUB_SOURCE_RE = re.compile(
    r"^(?!\.+/)[A-Za-z0-9_.-]{1,100}/(?!\.+$)[A-Za-z0-9_.-]{1,100}$")  # ni `.` ni `..`


class SearchError(Exception):
    """skills.sh est injoignable ou a répondu autre chose que du JSON."""


def search_skills(query: str, limit: int = config.MAX_LIMIT) -> list[dict]:
    """Interroge skills.sh et renvoie ses `limit` premiers candidats, dans
    l'ordre de pertinence de skills.sh. `relevance_rank` garde cette position
    (0 = premier résultat de l'API). Le nombre d'installations ne sert jamais
    à choisir qui est tronqué : trier par popularité faisait passer des skills
    génériques très installés devant les skills qui répondent au besoin."""
    try:
        data = net.get_json(SEARCH_URL.format(quote(query)))
    except (OSError, ValueError) as e:  # URLError ⊂ OSError ; JSON invalide ⊂ ValueError
        raise SearchError(f"skills.sh injoignable ou illisible : {e}") from e
    items = data.get("skills", []) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SearchError("skills.sh a renvoyé une réponse d'une forme inattendue")
    out = [c for c in (_candidate(s, rank) for rank, s in enumerate(items)) if c]
    return out[:limit]


def search_many(queries: list[str]) -> tuple[list[dict], list[str]]:
    """Plusieurs recherches fusionnées : chaque skill (source + identifiant)
    garde son meilleur rang parmi toutes les requêtes ; à rang égal, le plus
    installé passe devant. Renvoie (candidats, requêtes en échec). Lève
    SearchError seulement si toutes les requêtes échouent."""
    best: dict[tuple[str, str], dict] = {}
    failures: list[str] = []
    for q in queries:
        try:
            found = search_skills(q)
        except SearchError as e:
            failures.append(f"{q!r} : {e}")
            continue
        for c in found:
            key = (c["source"].lower(), c["skill_id"].lower())
            kept = best.get(key)
            if kept is None or c.get("relevance_rank", 0) < kept.get("relevance_rank", 0):
                best[key] = c
    if failures and len(failures) == len(queries):
        raise SearchError("; ".join(failures))
    out = sorted(best.values(),
                 key=lambda c: (c.get("relevance_rank", 0), -c.get("installs", 0)))
    return out, failures


def _candidate(s, rank: int) -> dict | None:
    """Un résultat de skills.sh en candidat, ou None s'il est mal formé :
    `source` et l'identifiant (`skillId`, sinon `name`) doivent être du texte
    non vide. Une entrée étrange est ignorée, jamais fatale pour les autres."""
    if not isinstance(s, dict):
        return None
    source = s.get("source")
    sid = s.get("skillId") or s.get("name")
    if not (isinstance(source, str) and source and isinstance(sid, str) and sid):
        return None
    name = s.get("name")
    return {"skill_id": sid, "name": name if isinstance(name, str) else "",
            "source": source, "installs": _as_int(s.get("installs")),
            "relevance_rank": rank}


def is_github_source(source: str) -> bool:
    return bool(GITHUB_SOURCE_RE.match(source or ""))


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):   # "1.2k", None, [], Infinity
        return 0
