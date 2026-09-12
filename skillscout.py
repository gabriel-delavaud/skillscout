"""skillscout — trie les skills de skills.sh par confiance, puis fait expliquer
un top 3 par un LLM local. Bibliothèque standard uniquement."""

import contextlib
import datetime as _dt
import json
import math
import sqlite3
import subprocess
import time
from urllib.error import HTTPError
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
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " kind TEXT, key TEXT, value TEXT, fetched_at REAL,"
                " PRIMARY KEY (kind, key))"
            )

    def get(self, kind: str, key: str) -> dict | None:
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT value, fetched_at FROM entries WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        if not row or time.time() - row[1] > CACHE_TTL:
            return None
        return json.loads(row[0])

    def put(self, kind: str, key: str, value: dict) -> None:
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
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
            # Identité authentifiée par l'API : `source` vient de skills.sh et
            # peut être périmé si le dépôt a été renommé/transféré depuis —
            # `gh api repos/{source}` suit silencieusement la redirection.
            "full_name": raw.get("full_name") or "",
            "owner_login": (raw.get("owner") or {}).get("login") or "",
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


def _fetch_tree_cached(source: str, cache: Cache) -> dict:
    def build():
        raw = gh_json(f"repos/{source}/git/trees/HEAD?recursive=1")
        return {
            "paths": [t["path"] for t in raw.get("tree", []) if t.get("path")],
            # GitHub tronque silencieusement au-delà d'environ 100 000 entrées
            # ou 7 Mo : les entrées omises sont justement celles où un script
            # aurait pu se cacher.
            "truncated": bool(raw.get("truncated")),
        }
    return _cached(cache, "tree", source, build)


def fetch_tree(source: str, cache: Cache) -> list[str]:
    return _fetch_tree_cached(source, cache)["paths"]


def fetch_tree_truncated(source: str, cache: Cache) -> bool:
    """Indique si l'arborescence renvoyée par `fetch_tree` est tronquée par
    GitHub, et donc incomplète — partage le même cache que `fetch_tree`."""
    return _fetch_tree_cached(source, cache)["truncated"]


EXEC_SUFFIXES = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts",
                 ".rb", ".pl", ".ps1", ".bat", ".command", ".ipynb", ".go",
                 ".rs", ".php")
EXEC_DIRS = ("scripts", "hooks", "bin")

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
             paths: list[str], now: float, truncated: bool = False) -> dict:
    """Applique l'exclusion stricte puis calcule le score de classement."""
    # L'identité vient de l'API (`repo_meta`), jamais de la chaîne `source`
    # fournie par skills.sh : `gh api repos/{source}` suit silencieusement un
    # renommage/transfert, donc `source` peut pointer vers un autre dépôt que
    # celui réellement interrogé.
    owner = repo_meta.get("owner_login") or cand["source"].split("/")[0]
    full_name = repo_meta.get("full_name") or ""

    out = dict(cand, excluded=False, reason=None, score=0.0, flags=[])

    if full_name and full_name.lower() != cand["source"].lower():
        out["excluded"] = True
        out["reason"] = (
            f"le dépôt {cand['source']} redirige vers {full_name} : son "
            f"identité ne peut pas être confirmée"
        )
        return out

    trusted = is_trusted_publisher(owner, owner_meta, repo_meta, now)

    if truncated and not trusted:
        out["excluded"] = True
        out["reason"] = (
            "arborescence du dépôt tronquée par GitHub : la liste des "
            "fichiers est incomplète et ne peut pas garantir l'absence de "
            "code exécutable"
        )
        return out

    execs = find_executables(paths)

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
            flags.append(
                f"organisation : ≥{MIN_OWNER_AGE_DAYS} j, "
                f"≥{MIN_PUBLIC_REPOS} dépôts publics, dépôt actif"
            )

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
        mot = "fichier exécutable" if len(execs) == 1 else "fichiers exécutables"
        flags.append(f"⚠ {len(execs)} {mot}")
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


import argparse
import os
import sys

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"
CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "skillscout", "cache.db")

TOP_N_FOR_LLM = 5

# Ollama applique son propre num_ctx par défaut (4096, sauf si le modelfile le
# relève) et tronque silencieusement, sans erreur. 5 corps de 3000 car.
# (~750 tokens chacun à ~4 car./token) + le prompt (~400 tokens) + la place
# pour la réponse : 8192 couvre ça avec de la marge.
NUM_CTX = 8192

PROMPT = """Tu conseilles un développeur francophone qui cherche un skill d'agent.

Son besoin : {need}

Voici {n} candidats, déjà filtrés sur leur provenance (l'éditeur est fiable ou
le dépôt ne contient aucun code exécutable). Ton travail n'est PAS de juger leur
sûreté — c'est fait — mais de dire lesquels répondent réellement au besoin.

{blocks}

Réponds en français, en {n} points numérotés maximum, du plus adapté au moins
adapté. Pour chacun : son identifiant, une phrase sur ce qu'il fait, et surtout
ce qui le distingue des autres candidats listés ci-dessus. Si aucun ne répond
au besoin, dis-le franchement au lieu d'en recommander un par défaut."""


class OllamaError(Exception):
    """Le serveur Ollama local est injoignable, le modèle demandé n'est pas
    installé, ou le serveur a répondu en erreur."""


def ask_qwen(need: str, skills: list[dict], model: str = DEFAULT_MODEL) -> str:
    skills = skills[:TOP_N_FOR_LLM]
    blocks = "\n\n".join(
        f"--- {s['skill_id']} ({s['source']}, score {s['score']}, "
        f"{', '.join(s.get('flags', []))})\n{s.get('body', '')}"
        for s in skills
    )
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user",
                      "content": PROMPT.format(need=need, n=len(skills),
                                               blocks=blocks)}],
        "options": {"num_ctx": NUM_CTX},
    }
    req = Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())
    except HTTPError as e:
        if e.code == 404:
            raise OllamaError(
                f"Modèle « {model} » introuvable sur Ollama. Récupérez-le "
                f"avec `ollama pull {model}`."
            ) from e
        raise OllamaError(
            f"Ollama a répondu une erreur HTTP {e.code} sur {OLLAMA_URL}."
        ) from e
    except OSError as e:
        raise OllamaError(
            f"Ollama injoignable sur {OLLAMA_URL}. Lancez `ollama serve`, "
            f"ou utilisez --no-llm."
        ) from e
    return (data.get("message") or {}).get("content", "").strip()


def format_top10(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:2}. {r['skill_id']:<34} {r['score']:>6.1f}  "
            f"{r['source']:<32} {' · '.join(r.get('flags', []))}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout",
        description="Trie les skills de skills.sh par confiance, puis fait "
                    "expliquer un top 3 par un LLM local.")
    ap.add_argument("besoin", help="ce que le skill doit savoir faire")
    ap.add_argument("--no-llm", action="store_true",
                    help="top 10 brut, sans passer par Qwen")
    ap.add_argument("--json", action="store_true", help="sortie machine")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=25,
                    help="candidats examinés (borne les appels GitHub)")
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    cache = Cache(CACHE_PATH)
    now = time.time()

    candidates = search_skills(args.besoin, limit=args.limit)
    if not candidates:
        print("Aucun candidat sur skills.sh pour cette recherche.", file=sys.stderr)
        return 1

    evaluated, excluded = [], 0
    for c in candidates:
        try:
            repo_meta = fetch_repo(c["source"], cache)
            # L'éditeur s'identifie depuis la réponse de l'API (`owner_login`),
            # jamais depuis la chaîne `source` de skills.sh, qui peut être
            # périmée si le dépôt a été renommé ou transféré.
            owner = repo_meta.get("owner_login") or c["source"].split("/")[0]
            owner_meta = fetch_owner(owner, cache)
            paths = fetch_tree(c["source"], cache)
            truncated = fetch_tree_truncated(c["source"], cache)
        except GhError as e:
            print(f"  ignoré {c['source']} : {e}", file=sys.stderr)
            continue
        row = evaluate(c, repo_meta, owner_meta, paths, now, truncated)
        row["default_branch"] = repo_meta["default_branch"]
        row["paths"] = paths
        if row["excluded"]:
            excluded += 1
        evaluated.append(row)

    top = rank(evaluated, top=10)
    if not top:
        print(f"Les {excluded} candidat(s) examiné(s) ont tous été écartés.",
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "paths"}
                          for r in top], ensure_ascii=False, indent=2))
        return 0

    print(f"\nTOP 10 par confiance ({excluded} écarté(s) sur "
          f"{len(candidates)} examiné(s))\n")
    print(format_top10(top))

    if args.no_llm:
        return 0

    for r in top:
        path = locate_skill_md(r["paths"], r["skill_id"])
        if path is None:
            r["body"] = "(SKILL.md introuvable — index skills.sh probablement périmé)"
            continue
        try:
            r["body"] = fetch_skill_md(r["source"], r["default_branch"], path)
        except OSError:
            r["body"] = "(SKILL.md illisible)"

    try:
        print(f"\n--- Analyse par {args.model} (local, gratuit) ---\n")
        print(ask_qwen(args.besoin, top, model=args.model))
    except OllamaError as e:
        print(f"\n{e}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
