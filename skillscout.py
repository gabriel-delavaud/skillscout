"""skillscout — trie les skills de skills.sh par confiance, puis fait expliquer
un top 3 par un LLM local. Bibliothèque standard uniquement."""

import datetime as _dt
import json
import math
import sqlite3
import subprocess
import time
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


CACHE_TTL = 7 * 86400
GH_TIMEOUT = 30


class GhError(Exception):
    """`gh` est absent, non authentifié, ou a répondu en erreur."""


class Cache:
    """Cache clé-valeur SQLite avec péremption. `kind` sépare les espaces de noms."""

    def __init__(self, path: str):
        self.path = path
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " kind TEXT, key TEXT, value TEXT, fetched_at REAL,"
                " PRIMARY KEY (kind, key))"
            )

    def get(self, kind: str, key: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT value, fetched_at FROM entries WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        if not row or time.time() - row[1] > CACHE_TTL:
            return None
        return json.loads(row[0])

    def put(self, kind: str, key: str, value: dict) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?)",
                (kind, key, json.dumps(value), time.time()),
            )


def gh_json(api_path: str):
    """Lance `gh api <api_path>` et renvoie le JSON. 5000 req/h, contre 60 en anonyme."""
    try:
        p = subprocess.run(["gh", "api", api_path], capture_output=True,
                           text=True, timeout=GH_TIMEOUT)
    except FileNotFoundError as e:
        raise GhError("`gh` introuvable. Installez GitHub CLI et lancez `gh auth login`.") from e
    except subprocess.TimeoutExpired as e:
        raise GhError(f"`gh api {api_path}` a dépassé le délai.") from e
    if p.returncode != 0:
        raise GhError(f"`gh api {api_path}` a échoué : {p.stderr.strip()}")
    return json.loads(p.stdout)


def _cached(cache: Cache, kind: str, key: str, build):
    hit = cache.get(kind, key)
    if hit is not None:
        return hit
    value = build()
    cache.put(kind, key, value)
    return value


def fetch_repo(source: str, cache: Cache) -> dict:
    def build():
        raw = gh_json(f"repos/{source}")
        return {
            "stars": int(raw.get("stargazers_count") or 0),
            "pushed_at": raw.get("pushed_at") or "",
            "owner_type": (raw.get("owner") or {}).get("type") or "User",
            "default_branch": raw.get("default_branch") or "main",
        }
    return _cached(cache, "repo", source, build)


def fetch_owner(owner: str, cache: Cache) -> dict:
    def build():
        raw = gh_json(f"users/{owner}")
        return {
            "type": raw.get("type") or "User",
            "created_at": raw.get("created_at") or "",
            "public_repos": int(raw.get("public_repos") or 0),
        }
    return _cached(cache, "owner", owner, build)


def fetch_tree(source: str, cache: Cache) -> list[str]:
    def build():
        raw = gh_json(f"repos/{source}/git/trees/HEAD?recursive=1")
        return {"paths": [t["path"] for t in raw.get("tree", []) if t.get("path")]}
    return _cached(cache, "tree", source, build)["paths"]


EXEC_SUFFIXES = (".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb",
                 ".pl", ".ps1", ".bat", ".command")
EXEC_DIRS = ("scripts", "hooks")

TRUSTED_PUBLISHERS = frozenset({
    "anthropics", "vercel", "vercel-labs", "etalab-ia", "firebase",
    "google", "googleapis", "microsoft", "cloudflare", "supabase",
    "stripe", "obra", "pbakaus",
})

MIN_OWNER_AGE_DAYS = 365
MIN_PUBLIC_REPOS = 10
MAX_STALE_DAYS = 365


def find_executables(paths: list[str]) -> list[str]:
    """Chemins constituant une surface d'exécution : extension à risque, ou
    situés sous un répertoire `scripts/` ou `hooks/`."""
    hits = []
    for p in paths:
        parts = p.split("/")
        in_exec_dir = any(seg in EXEC_DIRS for seg in parts[:-1])
        if p.endswith(EXEC_SUFFIXES) or in_exec_dir:
            hits.append(p)
    return hits


def _age_days(iso: str, now: float) -> float:
    """Jours écoulés depuis un horodatage ISO. Renvoie l'infini si illisible,
    pour que l'absence de donnée échoue les seuils au lieu de les passer."""
    if not iso:
        return float("inf")
    try:
        ts = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ") \
                         .replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return float("inf")
    return (now - ts) / 86400


def is_trusted_publisher(owner: str, owner_meta: dict, repo_meta: dict,
                         now: float) -> bool:
    """Liste blanche d'éditeurs, ou organisation passant les trois seuils."""
    if owner.lower() in TRUSTED_PUBLISHERS:
        return True
    if owner_meta.get("type") != "Organization":
        return False
    age = _age_days(owner_meta.get("created_at", ""), now)
    if age == float("inf"):
        return False
    return (
        age >= MIN_OWNER_AGE_DAYS
        and owner_meta.get("public_repos", 0) >= MIN_PUBLIC_REPOS
        and _age_days(repo_meta.get("pushed_at", ""), now) <= MAX_STALE_DAYS
    )


EXEC_PENALTY = 20.0


def evaluate(cand: dict, repo_meta: dict, owner_meta: dict,
             paths: list[str], now: float) -> dict:
    """Applique l'exclusion stricte puis calcule le score de classement."""
    owner = cand["source"].split("/")[0]
    execs = find_executables(paths)
    trusted = is_trusted_publisher(owner, owner_meta, repo_meta, now)

    out = dict(cand, excluded=False, reason=None, score=0.0, flags=[])

    if execs and not trusted:
        out["excluded"] = True
        out["reason"] = (f"{len(execs)} fichier(s) exécutable(s) et éditeur "
                         f"non vérifié ({owner})")
        return out

    score = 0.0
    flags: list[str] = []

    if owner.lower() in TRUSTED_PUBLISHERS:
        score += 50.0
        flags.append("éditeur en liste blanche")
    elif owner_meta.get("type") == "Organization":
        score += 15.0
        if trusted:
            flags.append("org vérifiée")

    if _age_days(owner_meta.get("created_at", ""), now) >= MIN_OWNER_AGE_DAYS:
        score += 10.0
    if owner_meta.get("public_repos", 0) >= MIN_PUBLIC_REPOS:
        score += 5.0

    score += min(15.0, 5.0 * math.log10(1 + repo_meta.get("stars", 0)))

    stale = _age_days(repo_meta.get("pushed_at", ""), now)
    if stale <= 90:
        score += 10.0
    elif stale <= MAX_STALE_DAYS:
        score += 5.0
    else:
        flags.append("⚠ non maintenu depuis plus d'un an")

    score += min(10.0, 2.5 * math.log10(1 + cand.get("installs", 0)))

    if execs:
        score -= EXEC_PENALTY
        flags.append(f"⚠ {len(execs)} fichiers exécutables")
    else:
        flags.append("markdown pur")

    out["score"] = round(score, 2)
    out["flags"] = flags
    return out


def rank(evaluated: list[dict], top: int = 10) -> list[dict]:
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: e["score"], reverse=True)
    return kept[:top]


RAW_URL = "https://raw.githubusercontent.com/{source}/{branch}/{path}"
SKILL_MD_LIMIT = 3000


def locate_skill_md(paths: list[str], skill_id: str) -> str | None:
    """Retrouve le SKILL.md d'un skill donné dans l'arborescence du dépôt.
    Préfère le répertoire portant le nom du skill ; se replie sur la racine
    quand le dépôt n'expose qu'un seul skill."""
    candidates = [p for p in paths if p.endswith("SKILL.md")]
    for p in candidates:
        parts = p.split("/")
        if len(parts) >= 2 and parts[-2] == skill_id:
            return p
    if candidates == ["SKILL.md"]:
        return "SKILL.md"
    return None


def fetch_skill_md(source: str, branch: str, path: str,
                   limit: int = SKILL_MD_LIMIT) -> str:
    url = RAW_URL.format(source=source, branch=branch, path=quote(path))
    req = Request(url, headers={"User-Agent": "skillscout"})
    with urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")[:limit]
