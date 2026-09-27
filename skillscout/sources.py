"""Recherche de skills sur skills.sh."""
from __future__ import annotations

import json
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
    items = data.get("skills", []) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SearchError("skills.sh a renvoyé une réponse d'une forme inattendue")
    out = [c for c in (_candidate(s, rank) for rank, s in enumerate(items)) if c]
    out.sort(key=lambda s: s["installs"], reverse=True)
    return out[:limit]


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


LEADERBOARD_URL = "https://www.skills.sh/{}"
LEADERBOARDS = ("trending", "hot")
LEADERBOARD_MAX_CHARS = 4_000_000

# Les pages de classement n'ont pas d'API : leur liste est embarquée dans le
# flux RSC de Next.js, une chaîne JavaScript passée à `self.__next_f.push`.
_RSC_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_INITIAL_SKILLS = '"initialSkills":'


class LeaderboardError(Exception):
    """Classement injoignable, ou dont le format a changé."""


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):   # "1.2k", None, [], Infinity
        return 0


def parse_initial_skills(html: str) -> list[dict]:
    """Candidats de la liste `initialSkills`, dans l'ordre de la page."""
    try:
        flight = "".join(json.loads('"' + chunk + '"') for chunk in _RSC_PUSH.findall(html))
    except ValueError as e:
        raise LeaderboardError("flux RSC illisible") from e
    start = flight.find(_INITIAL_SKILLS)
    if start < 0:
        raise LeaderboardError("liste initialSkills absente : le format de la page a changé")
    try:
        items, _ = json.JSONDecoder().raw_decode(flight, start + len(_INITIAL_SKILLS))
    except ValueError as e:
        raise LeaderboardError("liste initialSkills illisible") from e
    if not isinstance(items, list):
        raise LeaderboardError("initialSkills n'est pas une liste")
    return [c for c in (_candidate(s, rank) for rank, s in enumerate(items)) if c]


def fetch_leaderboard(kind: str, top: int = 50) -> list[dict]:
    if kind not in LEADERBOARDS:
        raise ValueError(f"classement inconnu : {kind}")
    try:
        html = net.get_text(LEADERBOARD_URL.format(kind), LEADERBOARD_MAX_CHARS)
    except OSError as e:
        raise LeaderboardError(f"classement {kind} injoignable : {e}") from e
    return parse_initial_skills(html)[:top]
