"""Accès GitHub via `gh`, avec cache SQLite."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import sqlite3
import subprocess
import time
from urllib.parse import quote

from . import config, net, trust

GH_TIMEOUT = 30

CACHE_TTL = 7 * 86400        # éditeur, arborescence, blobs
CACHE_TTL_REPO = 86400       # métadonnées de dépôt : `pushed_at` pilote la
                             # relecture de l'arbre, il doit être vu vite
CACHE_TTL_BLOB = 30 * 86400  # un blob est adressé par son SHA : immuable

# Incrémenté chaque fois que la forme des dicts mis en cache change. Fold dans
# le namespace par `_cached` : toute entrée écrite sous un schéma antérieur
# devient invisible d'un coup, sans migration ni purge manuelle.
#   v2 : owner_login/full_name sur "repo", truncated sur "tree"
#   v3 : created_at sur "repo", source_repos sur "owner", sha/blobs sur "tree"
#   v4 : exec_bits sur "tree" (un arbre sans ce champ ne sait pas, il ne dit pas « aucun »)
#   v5 : opaque_entries sur "tree" (liens symboliques et sous-modules)
CACHE_SCHEMA = 5

RAW_URL = "https://raw.githubusercontent.com/{source}/{branch}/{path}"

# Sous pythonw (tâche planifiée), chaque `gh` ouvrirait sinon une console.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GhError(Exception):
    """`gh` est absent, non authentifié, ou a répondu en erreur."""


class Cache:
    """Cache clé-valeur SQLite avec péremption. `kind` sépare les espaces de
    noms ; la durée de vie se choisit à la lecture, par type de donnée."""

    def __init__(self, path: str):
        self.path = path
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                " kind TEXT, key TEXT, value TEXT, fetched_at REAL,"
                " PRIMARY KEY (kind, key))"
            )

    def get(self, kind: str, key: str, ttl: float = CACHE_TTL) -> dict | None:
        with contextlib.closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT value, fetched_at FROM entries WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
        if not row or time.time() - row[1] > ttl:
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
        # `gh` écrit en UTF-8 : sans encodage explicite, Windows décode en cp1252
        # et plante sur le premier caractère non latin.
        p = subprocess.run(["gh", "api", api_path], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=GH_TIMEOUT, creationflags=_NO_WINDOW)
    except FileNotFoundError as e:
        raise GhError("`gh` introuvable. Installez GitHub CLI et lancez `gh auth login`.") from e
    except subprocess.TimeoutExpired as e:
        raise GhError(f"`gh api {api_path}` a dépassé le délai.") from e
    if p.returncode != 0:
        raise GhError(f"`gh api {api_path}` a échoué : {p.stderr.strip()}")
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        # `gh` imprime parfois du texte (limite de débit, avertissement de
        # mise à jour) : un traceback brut n'aiderait personne.
        raise GhError(f"`gh api {api_path}` a renvoyé autre chose que du JSON : "
                      f"{p.stdout.strip()[:80]!r}") from e


def _cached(cache: Cache, kind: str, key: str, build, ttl: float = CACHE_TTL):
    kind = f"{kind}:v{CACHE_SCHEMA}"
    hit = cache.get(kind, key, ttl)
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
            "created_at": raw.get("created_at") or "",
            "owner_type": (raw.get("owner") or {}).get("type") or "User",
            "default_branch": raw.get("default_branch") or "main",
            # Identité authentifiée par l'API : `source` vient de skills.sh et
            # peut être périmé si le dépôt a été renommé/transféré depuis —
            # `gh api repos/{source}` suit silencieusement la redirection.
            "full_name": raw.get("full_name") or "",
            "owner_login": (raw.get("owner") or {}).get("login") or "",
        }
    return _cached(cache, "repo", source, build, ttl=CACHE_TTL_REPO)


def fetch_owner(owner: str, cache: Cache) -> dict:
    def build():
        raw = gh_json(f"users/{owner}")
        out = {
            "type": raw.get("type") or "User",
            "created_at": raw.get("created_at") or "",
            "public_repos": int(raw.get("public_repos") or 0),
        }
        if out["type"] == "Organization":
            # `public_repos` compte les forks : dix forks se font en dix clics.
            # On ne retient que les dépôts d'origine, et on n'en demande que
            # le strict nécessaire pour trancher le seuil. En cas d'erreur,
            # 0 : échec en fermeture.
            try:
                srcs = gh_json(f"orgs/{owner}/repos?type=sources"
                               f"&per_page={trust.MIN_PUBLIC_REPOS}")
                out["source_repos"] = len(srcs) if isinstance(srcs, list) else 0
            except GhError:
                out["source_repos"] = 0
        return out
    return _cached(cache, "owner", owner, build)


def fetch_tree_snapshot(source: str, cache: Cache, pushed_at: str = "") -> dict:
    """Arborescence complète du dépôt : chemins, SHA de l'arbre, SHA de chaque
    blob, et indicateur de troncature. La clé de cache embarque `pushed_at` :
    dès qu'un push est constaté (métadonnées relues toutes les 24 h), l'arbre
    est relu au lieu de servir un instantané d'avant le push."""
    def build():
        raw = gh_json(f"repos/{source}/git/trees/HEAD?recursive=1")
        entries = [t for t in raw.get("tree", []) if t.get("path")]
        return {
            "sha": raw.get("sha") or "",
            "paths": [t["path"] for t in entries],
            "blobs": {t["path"]: t["sha"] for t in entries
                      if t.get("type") == "blob" and t.get("sha")},
            # Mode 100755 : le seul signal fiable d'un script sans extension.
            "exec_bits": [t["path"] for t in entries if t.get("mode") == "100755"],
            # Contenu absent de l'arbre : un lien symbolique (120000) ne stocke
            # que le chemin de sa cible, un sous-module (160000) pointe vers un
            # autre dépôt. Comme un arbre tronqué, ils ne se vérifient pas.
            "opaque_entries": [t["path"] for t in entries
                               if t.get("mode") in ("120000", "160000")],
            # GitHub tronque silencieusement au-delà d'environ 100 000 entrées
            # ou 7 Mo : les entrées omises sont justement celles où un script
            # aurait pu se cacher.
            "truncated": bool(raw.get("truncated")),
        }
    return _cached(cache, "tree", f"{source}@{pushed_at}", build)


def fetch_tree(source: str, cache: Cache, pushed_at: str = "") -> list[str]:
    return fetch_tree_snapshot(source, cache, pushed_at)["paths"]


def fetch_tree_truncated(source: str, cache: Cache, pushed_at: str = "") -> bool:
    """Indique si l'arborescence est tronquée par GitHub, donc incomplète.
    Une entrée dépourvue du champ est lue comme tronquée, jamais comme sûre."""
    return fetch_tree_snapshot(source, cache, pushed_at).get("truncated", True)


def fetch_blob(source: str, sha: str, cache: Cache,
               limit: int = config.SKILL_MD_SCAN_LIMIT + 1) -> str:
    """Contenu d'un blob épinglé par son SHA — exactement la version listée
    dans l'arbre évalué, quoi qu'il arrive à la branche entre-temps. Le
    plafond fait partie de la clé de cache : un texte coupé court n'est
    jamais resservi à une lecture qui en demande davantage."""
    def build():
        raw = gh_json(f"repos/{source}/git/blobs/{sha}")
        content = raw.get("content") or ""
        if raw.get("encoding") == "base64":
            try:
                content = base64.b64decode(content).decode("utf-8", "replace")
            except ValueError as e:  # binascii.Error hérite de ValueError
                # Un seul blob illisible ne doit pas faire tomber tout le lot.
                raise GhError(f"blob {sha} de {source} illisible : base64 invalide") from e
        return {"text": content[:limit]}
    return _cached(cache, "blob", f"{sha}:{limit}", build,
                   ttl=CACHE_TTL_BLOB)["text"]


def fetch_skill_md(source: str, branch: str, path: str,
                   limit: int = config.SKILL_MD_SCAN_LIMIT + 1) -> str:
    """Repli non épinglé (branche mobile) quand l'arbre n'a pas donné de SHA.
    Renvoie au plus `limit` caractères."""
    url = RAW_URL.format(source=source, branch=branch, path=quote(path))
    return net.get_text(url, limit)


def git_blob_sha(data: bytes) -> str:
    """Empreinte Git d'un blob : sha1(b"blob <taille>\\0" + contenu)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_blob_bytes(source: str, sha: str, cache: Cache) -> bytes:
    """Octets exacts d'un blob, vérifiés contre son empreinte : c'est ce qui
    sera écrit sur le disque à l'installation. Un contenu qui ne correspond
    pas au SHA demandé n'est ni renvoyé ni mis en cache."""
    def build():
        raw = gh_json(f"repos/{source}/git/blobs/{sha}")
        if raw.get("encoding") != "base64":
            raise GhError(f"blob {sha} de {source} : encodage inattendu")
        b64 = "".join((raw.get("content") or "").split())   # GitHub coupe à 60 colonnes
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError as e:
            raise GhError(f"blob {sha} de {source} illisible : base64 invalide") from e
        if git_blob_sha(data) != sha:
            raise GhError(f"blob {sha} de {source} : empreinte différente du contenu reçu")
        return {"b64": b64}
    return base64.b64decode(_cached(cache, "blobbytes", sha, build, ttl=CACHE_TTL_BLOB)["b64"])
