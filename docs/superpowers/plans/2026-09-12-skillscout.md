# skillscout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Une commande shell `skillscout "<besoin>"` qui trie les skills de skills.sh par confiance sans LLM, puis fait expliquer un top 3 par `qwen3:8b` en local — zéro token facturé.

**Architecture :** Un module Python unique (option A) en cinq étages : recherche skills.sh → métadonnées GitHub via `gh api` avec cache SQLite → exclusion et score déterministes → récupération des SKILL.md → synthèse par Ollama. Les quatre premiers étages sont gratuits et reproductibles ; seul le dernier fait tourner un modèle, sur la machine.

**Tech Stack :** Python 3.14 (bibliothèque standard seule : `urllib`, `sqlite3`, `json`, `subprocess`, `unittest`), `gh` CLI authentifié, Ollama + `qwen3:8b`.

**Spec :** `docs/superpowers/specs/2026-09-12-skillscout-design.md`

## Global Constraints

- Python ≥ 3.11, **bibliothèque standard uniquement** — aucun `pip install`, aucune dépendance externe.
- Tests avec **`unittest`**, lancés par `python3 -m unittest discover -s tests -v`. Pas pytest : absent de la machine, et Python 3.14 refuse les installations système.
- **Aucun appel réseau dans les tests.** `urlopen` et `subprocess.run` sont systématiquement simulés via `unittest.mock.patch`.
- L'accès GitHub passe **exclusivement par `gh api`** (5 000 req/h). Jamais de repli sur l'API anonyme (60 req/h), même en cas d'échec.
- Toutes les chaînes destinées à l'utilisateur sont **en français**.
- Liste blanche verbatim : `anthropics, vercel, vercel-labs, etalab-ia, firebase, google, googleapis, microsoft, cloudflare, supabase, stripe, obra, pbakaus`.
- Seuils verbatim : âge du compte ≥ **365** jours, dépôts publics ≥ **10**, `pushed_at` ≤ **365** jours.
- Cache SQLite avec TTL de **7 jours**, dans `cache.db` (gitignoré).
- Chaque SKILL.md est tronqué à **3000** caractères avant d'être envoyé à Qwen.

---

### Task 1: Recherche skills.sh

**Files:**
- Create: `skillscout.py`
- Test: `tests/test_skillscout.py`

**Interfaces:**
- Consumes: rien.
- Produces: `search_skills(query: str, limit: int = 25) -> list[dict]`. Chaque dict a exactement les clés `skill_id: str`, `name: str`, `source: str`, `installs: int`. Trié par `installs` décroissant, tronqué à `limit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skillscout.py
import json, sys, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import skillscout


def fake_urlopen(payload: dict):
    """Retourne un contexte simulant urllib.request.urlopen."""
    cm = MagicMock()
    cm.read.return_value = json.dumps(payload).encode()
    cm.__enter__ = lambda s: cm
    cm.__exit__ = lambda s, *a: False
    return cm


class TestSearchSkills(unittest.TestCase):
    PAYLOAD = {"skills": [
        {"id": "a/b/petit", "skillId": "petit", "name": "petit", "installs": 5, "source": "a/b"},
        {"id": "c/d/gros", "skillId": "gros", "name": "gros", "installs": 900, "source": "c/d"},
        {"id": "e/f/moyen", "skillId": "moyen", "name": "moyen", "installs": 50, "source": "e/f"},
    ]}

    def test_trie_par_installs_decroissant(self):
        with patch("skillscout.urlopen", return_value=fake_urlopen(self.PAYLOAD)):
            out = skillscout.search_skills("sécurité")
        self.assertEqual([s["skill_id"] for s in out], ["gros", "moyen", "petit"])

    def test_respecte_la_limite(self):
        with patch("skillscout.urlopen", return_value=fake_urlopen(self.PAYLOAD)):
            out = skillscout.search_skills("sécurité", limit=2)
        self.assertEqual(len(out), 2)

    def test_forme_des_dicts(self):
        with patch("skillscout.urlopen", return_value=fake_urlopen(self.PAYLOAD)):
            out = skillscout.search_skills("sécurité")
        self.assertEqual(set(out[0]), {"skill_id", "name", "source", "installs"})

    def test_liste_vide_si_aucun_resultat(self):
        with patch("skillscout.urlopen", return_value=fake_urlopen({"skills": []})):
            self.assertEqual(skillscout.search_skills("xyzzy"), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'skillscout'`

- [ ] **Step 3: Write minimal implementation**

```python
# skillscout.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(search): interroge skills.sh et trie par installations"
```

---

### Task 2: Client GitHub avec cache SQLite

**Files:**
- Modify: `skillscout.py`
- Modify: `tests/test_skillscout.py`

**Interfaces:**
- Consumes: rien de la Task 1.
- Produces:
  - `Cache(path: str)` avec `get(kind: str, key: str) -> dict | None` et `put(kind: str, key: str, value: dict) -> None`. Une entrée de plus de 7 jours est traitée comme absente.
  - `gh_json(api_path: str) -> dict | list` — lance `gh api <api_path>`. Lève `GhError` si `gh` est absent ou en échec.
  - `fetch_repo(source: str, cache: Cache) -> dict` → clés `stars: int`, `pushed_at: str`, `owner_type: str`, `default_branch: str`.
  - `fetch_owner(owner: str, cache: Cache) -> dict` → clés `type: str`, `created_at: str`, `public_repos: int`.
  - `fetch_tree(source: str, cache: Cache) -> list[str]` — liste de chemins.
  - `GhError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# à ajouter dans tests/test_skillscout.py
import subprocess, tempfile, os


class TestCache(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "c.db")
        self.cache = skillscout.Cache(self.path)

    def test_absent_renvoie_none(self):
        self.assertIsNone(self.cache.get("repo", "a/b"))

    def test_aller_retour(self):
        self.cache.put("repo", "a/b", {"stars": 7})
        self.assertEqual(self.cache.get("repo", "a/b"), {"stars": 7})

    def test_entree_perimee_traitee_comme_absente(self):
        self.cache.put("repo", "a/b", {"stars": 7})
        with skillscout.sqlite3.connect(self.path) as db:
            db.execute("UPDATE entries SET fetched_at = fetched_at - ?",
                       (8 * 86400,))
        self.assertIsNone(self.cache.get("repo", "a/b"))


class TestGhJson(unittest.TestCase):
    def test_parse_la_sortie_de_gh(self):
        done = subprocess.CompletedProcess([], 0, stdout='{"stargazers_count": 3}', stderr="")
        with patch("skillscout.subprocess.run", return_value=done):
            self.assertEqual(skillscout.gh_json("repos/a/b"), {"stargazers_count": 3})

    def test_leve_gherror_si_gh_echoue(self):
        done = subprocess.CompletedProcess([], 1, stdout="", stderr="gh: not found")
        with patch("skillscout.subprocess.run", return_value=done):
            with self.assertRaises(skillscout.GhError):
                skillscout.gh_json("repos/a/b")

    def test_leve_gherror_si_gh_absent(self):
        with patch("skillscout.subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(skillscout.GhError):
                skillscout.gh_json("repos/a/b")


class TestFetchers(unittest.TestCase):
    def setUp(self):
        self.cache = skillscout.Cache(os.path.join(tempfile.mkdtemp(), "c.db"))

    def test_fetch_repo_normalise(self):
        raw = {"stargazers_count": 42, "pushed_at": "2026-09-01T00:00:00Z",
               "owner": {"type": "Organization"}, "default_branch": "main"}
        with patch("skillscout.gh_json", return_value=raw):
            out = skillscout.fetch_repo("a/b", self.cache)
        self.assertEqual(out, {"stars": 42, "pushed_at": "2026-09-01T00:00:00Z",
                               "owner_type": "Organization", "default_branch": "main"})

    def test_fetch_repo_sert_le_cache_sans_rappeler_gh(self):
        raw = {"stargazers_count": 1, "pushed_at": "2026-01-01T00:00:00Z",
               "owner": {"type": "User"}, "default_branch": "main"}
        with patch("skillscout.gh_json", return_value=raw) as g:
            skillscout.fetch_repo("a/b", self.cache)
            skillscout.fetch_repo("a/b", self.cache)
        self.assertEqual(g.call_count, 1)

    def test_fetch_owner_normalise(self):
        raw = {"type": "Organization", "created_at": "2020-01-01T00:00:00Z",
               "public_repos": 73}
        with patch("skillscout.gh_json", return_value=raw):
            out = skillscout.fetch_owner("etalab-ia", self.cache)
        self.assertEqual(out, {"type": "Organization",
                               "created_at": "2020-01-01T00:00:00Z",
                               "public_repos": 73})

    def test_fetch_tree_renvoie_les_chemins(self):
        raw = {"tree": [{"path": "SKILL.md"}, {"path": "scripts/run.sh"}]}
        with patch("skillscout.gh_json", return_value=raw):
            out = skillscout.fetch_tree("a/b", self.cache)
        self.assertEqual(out, ["SKILL.md", "scripts/run.sh"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'skillscout' has no attribute 'Cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# à ajouter dans skillscout.py, sous les imports existants
import sqlite3
import subprocess
import time

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 14 tests au total

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(github): client gh api avec cache SQLite à péremption"
```

---

### Task 3: Détection d'exécutables et confiance de l'éditeur

**Files:**
- Modify: `skillscout.py`
- Modify: `tests/test_skillscout.py`

**Interfaces:**
- Consumes: `fetch_tree` (Task 2) fournit la liste de chemins ; `fetch_owner` fournit `{type, created_at, public_repos}` ; `fetch_repo` fournit `pushed_at`.
- Produces:
  - `EXEC_SUFFIXES: tuple[str, ...]`, `EXEC_DIRS: tuple[str, ...]`, `TRUSTED_PUBLISHERS: frozenset[str]`.
  - `find_executables(paths: list[str]) -> list[str]` — les chemins fautifs, dans l'ordre d'entrée.
  - `is_trusted_publisher(owner: str, owner_meta: dict, repo_meta: dict, now: float) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# à ajouter dans tests/test_skillscout.py
NOW = 1789000000.0  # ~2026-09-12


def iso_days_ago(days: float, now: float = NOW) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(now - days * 86400, dt.timezone.utc) \
             .strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFindExecutables(unittest.TestCase):
    def test_markdown_pur_ne_signale_rien(self):
        self.assertEqual(
            skillscout.find_executables(["SKILL.md", "references/checklist.md"]), [])

    def test_detecte_par_extension(self):
        self.assertEqual(
            skillscout.find_executables(["SKILL.md", "run.sh", "tool.py"]),
            ["run.sh", "tool.py"])

    def test_detecte_par_repertoire(self):
        self.assertEqual(
            skillscout.find_executables(["scripts/thing.txt", "hooks/x.json"]),
            ["scripts/thing.txt", "hooks/x.json"])

    def test_ne_confond_pas_un_nom_de_fichier_contenant_scripts(self):
        self.assertEqual(skillscout.find_executables(["docs/scripts-guide.md"]), [])


class TestIsTrustedPublisher(unittest.TestCase):
    FRESH = {"pushed_at": iso_days_ago(10)}

    def test_liste_blanche_passe_meme_si_particulier(self):
        # obra publie Superpowers depuis un compte personnel : la liste blanche
        # doit le couvrir, sinon un skill à 280k étoiles serait écarté.
        meta = {"type": "User", "created_at": iso_days_ago(200), "public_repos": 3}
        self.assertTrue(skillscout.is_trusted_publisher("obra", meta, self.FRESH, NOW))

    def test_liste_blanche_insensible_a_la_casse(self):
        meta = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 50}
        self.assertTrue(skillscout.is_trusted_publisher("Vercel", meta, self.FRESH, NOW))

    def test_particulier_hors_liste_echoue(self):
        meta = {"type": "User", "created_at": iso_days_ago(5000), "public_repos": 163}
        self.assertFalse(skillscout.is_trusted_publisher("biggora", meta, self.FRESH, NOW))

    def test_organisation_passant_les_trois_seuils(self):
        meta = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 73}
        self.assertTrue(skillscout.is_trusted_publisher("inconnue", meta, self.FRESH, NOW))

    def test_organisation_trop_jeune_echoue(self):
        meta = {"type": "Organization", "created_at": iso_days_ago(100), "public_repos": 73}
        self.assertFalse(skillscout.is_trusted_publisher("inconnue", meta, self.FRESH, NOW))

    def test_organisation_coquille_vide_echoue(self):
        meta = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 2}
        self.assertFalse(skillscout.is_trusted_publisher("inconnue", meta, self.FRESH, NOW))

    def test_organisation_au_depot_abandonne_echoue(self):
        meta = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 73}
        stale = {"pushed_at": iso_days_ago(400)}
        self.assertFalse(skillscout.is_trusted_publisher("inconnue", meta, stale, NOW))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'skillscout' has no attribute 'find_executables'`

- [ ] **Step 3: Write minimal implementation**

```python
# à ajouter dans skillscout.py
import datetime as _dt

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
    return (
        _age_days(owner_meta.get("created_at", ""), now) >= MIN_OWNER_AGE_DAYS
        and owner_meta.get("public_repos", 0) >= MIN_PUBLIC_REPOS
        and _age_days(repo_meta.get("pushed_at", ""), now) <= MAX_STALE_DAYS
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 25 tests au total

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(trust): détection d'exécutables et confiance de l'éditeur"
```

---

### Task 4: Exclusion, score et classement

**Files:**
- Modify: `skillscout.py`
- Modify: `tests/test_skillscout.py`

**Interfaces:**
- Consumes: `find_executables` et `is_trusted_publisher` (Task 3) ; les dicts de `search_skills` (Task 1) ; `fetch_repo`/`fetch_owner` (Task 2).
- Produces:
  - `evaluate(cand: dict, repo_meta: dict, owner_meta: dict, paths: list[str], now: float) -> dict` → clés `skill_id`, `name`, `source`, `installs`, `excluded: bool`, `reason: str | None`, `score: float`, `flags: list[str]`.
  - `rank(evaluated: list[dict], top: int = 10) -> list[dict]` — écarte les exclus, trie par `score` décroissant, tronque à `top`.

- [ ] **Step 1: Write the failing test**

```python
# à ajouter dans tests/test_skillscout.py
class TestEvaluate(unittest.TestCase):
    CAND = {"skill_id": "s", "name": "s", "source": "who/repo", "installs": 100}
    ORG_OK = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 73}
    USER = {"type": "User", "created_at": iso_days_ago(2000), "public_repos": 5}
    REPO = {"stars": 100, "pushed_at": iso_days_ago(10),
            "owner_type": "Organization", "default_branch": "main"}

    def test_particulier_avec_scripts_est_exclu(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.USER,
                                  ["SKILL.md", "run.sh"], NOW)
        self.assertTrue(out["excluded"])
        self.assertIn("exécutable", out["reason"])

    def test_particulier_sans_script_est_garde(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.USER,
                                  ["SKILL.md"], NOW)
        self.assertFalse(out["excluded"])

    def test_organisation_avec_scripts_est_gardee(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                  ["SKILL.md", "run.sh"], NOW)
        self.assertFalse(out["excluded"])

    def test_penalite_pour_les_scripts(self):
        avec = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                   ["SKILL.md", "run.sh"], NOW)["score"]
        sans = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                   ["SKILL.md"], NOW)["score"]
        self.assertAlmostEqual(sans - avec, 20.0, places=6)

    def test_liste_blanche_domine_le_score(self):
        cand = dict(self.CAND, source="vercel-labs/skills")
        listee = skillscout.evaluate(cand, self.REPO, self.ORG_OK, ["SKILL.md"], NOW)["score"]
        autre = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK, ["SKILL.md"], NOW)["score"]
        self.assertGreater(listee, autre + 30)

    def test_drapeaux_lisibles(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                  ["SKILL.md", "a.sh", "b.py"], NOW)
        self.assertIn("⚠ 2 fichiers exécutables", out["flags"])
        self.assertIn("org vérifiée", out["flags"])


class TestRank(unittest.TestCase):
    def test_ecarte_les_exclus_trie_et_tronque(self):
        rows = [
            {"skill_id": "a", "excluded": False, "score": 10.0},
            {"skill_id": "b", "excluded": True, "score": 99.0},
            {"skill_id": "c", "excluded": False, "score": 50.0},
        ]
        out = skillscout.rank(rows, top=2)
        self.assertEqual([r["skill_id"] for r in out], ["c", "a"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'skillscout' has no attribute 'evaluate'`

- [ ] **Step 3: Write minimal implementation**

```python
# à ajouter dans skillscout.py
import math

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 32 tests au total

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(score): exclusion stricte, score de confiance et classement"
```

---

### Task 5: Localisation et récupération des SKILL.md

**Files:**
- Modify: `skillscout.py`
- Modify: `tests/test_skillscout.py`

**Interfaces:**
- Consumes: la liste de chemins de `fetch_tree` (Task 2), `default_branch` de `fetch_repo`, `skill_id` des dicts de `rank` (Task 4).
- Produces:
  - `locate_skill_md(paths: list[str], skill_id: str) -> str | None` — chemin du SKILL.md correspondant, ou `None`.
  - `fetch_skill_md(source: str, branch: str, path: str, limit: int = 3000) -> str` — contenu tronqué à `limit` caractères.

- [ ] **Step 1: Write the failing test**

```python
# à ajouter dans tests/test_skillscout.py
class TestLocateSkillMd(unittest.TestCase):
    TREE = ["README.md", "skills/securite-developpement/SKILL.md",
            "skills/rgaa/SKILL.md", "SKILL.md"]

    def test_trouve_par_repertoire_nomme(self):
        self.assertEqual(
            skillscout.locate_skill_md(self.TREE, "rgaa"),
            "skills/rgaa/SKILL.md")

    def test_repli_sur_la_racine_si_un_seul_skill_md(self):
        self.assertEqual(
            skillscout.locate_skill_md(["SKILL.md", "README.md"], "peu-importe"),
            "SKILL.md")

    def test_none_si_introuvable(self):
        # Cas réel : skills.sh référence encore `securite-anssi`, renommé depuis.
        self.assertIsNone(skillscout.locate_skill_md(self.TREE, "securite-anssi"))


class TestFetchSkillMd(unittest.TestCase):
    def test_tronque_au_plafond(self):
        cm = MagicMock()
        cm.read.return_value = ("x" * 5000).encode()
        cm.__enter__ = lambda s: cm
        cm.__exit__ = lambda s, *a: False
        with patch("skillscout.urlopen", return_value=cm):
            out = skillscout.fetch_skill_md("a/b", "main", "SKILL.md", limit=100)
        self.assertEqual(len(out), 100)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'skillscout' has no attribute 'locate_skill_md'`

- [ ] **Step 3: Write minimal implementation**

```python
# à ajouter dans skillscout.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 36 tests au total

- [ ] **Step 5: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(content): localise et récupère les SKILL.md des candidats"
```

---

### Task 6: Synthèse Qwen, CLI et lanceur

**Files:**
- Modify: `skillscout.py`
- Modify: `tests/test_skillscout.py`
- Create: `~/bin/skillscout`

**Interfaces:**
- Consumes: tout ce qui précède — `search_skills`, `Cache`, `fetch_repo`/`fetch_owner`/`fetch_tree`, `evaluate`, `rank`, `locate_skill_md`, `fetch_skill_md`.
- Produces:
  - `ask_qwen(need: str, skills: list[dict], model: str = "qwen3:8b") -> str` — texte en français. Lève `OllamaError` si le serveur ne répond pas.
  - `OllamaError(Exception)`.
  - `format_top10(rows: list[dict]) -> str`.
  - `main(argv: list[str]) -> int` — code de sortie 0 si au moins un candidat survit, 1 sinon.

- [ ] **Step 1: Write the failing test**

```python
# à ajouter dans tests/test_skillscout.py
class TestAskQwen(unittest.TestCase):
    ROWS = [{"skill_id": "a", "source": "x/y", "score": 60.0,
             "flags": ["markdown pur"], "body": "# A\nfait des choses"}]

    def test_renvoie_le_message_du_modele(self):
        cm = MagicMock()
        cm.read.return_value = json.dumps(
            {"message": {"content": "1. a — le plus sûr"}}).encode()
        cm.__enter__ = lambda s: cm
        cm.__exit__ = lambda s, *a: False
        with patch("skillscout.urlopen", return_value=cm):
            out = skillscout.ask_qwen("sécurité", self.ROWS)
        self.assertIn("le plus sûr", out)

    def test_leve_ollamaerror_si_serveur_muet(self):
        with patch("skillscout.urlopen", side_effect=OSError("refusé")):
            with self.assertRaises(skillscout.OllamaError):
                skillscout.ask_qwen("sécurité", self.ROWS)


class TestFormatTop10(unittest.TestCase):
    def test_affiche_score_source_et_drapeaux(self):
        out = skillscout.format_top10([
            {"skill_id": "a", "source": "x/y", "score": 61.5,
             "flags": ["markdown pur"], "installs": 12}])
        self.assertIn("x/y", out)
        self.assertIn("61.5", out)
        self.assertIn("markdown pur", out)


class TestMain(unittest.TestCase):
    def test_no_llm_court_circuite_ollama(self):
        cand = [{"skill_id": "s", "name": "s", "source": "etalab-ia/skills", "installs": 13}]
        repo = {"stars": 18, "pushed_at": iso_days_ago(1),
                "owner_type": "Organization", "default_branch": "main"}
        owner = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 73}
        with patch("skillscout.search_skills", return_value=cand), \
             patch("skillscout.fetch_repo", return_value=repo), \
             patch("skillscout.fetch_owner", return_value=owner), \
             patch("skillscout.fetch_tree", return_value=["skills/s/SKILL.md"]), \
             patch("skillscout.ask_qwen") as q:
            code = skillscout.main(["--no-llm", "sécurité"])
        self.assertEqual(code, 0)
        q.assert_not_called()

    def test_code_1_si_tout_est_ecarte(self):
        cand = [{"skill_id": "s", "name": "s", "source": "inconnu/repo", "installs": 3}]
        repo = {"stars": 0, "pushed_at": iso_days_ago(900),
                "owner_type": "User", "default_branch": "main"}
        owner = {"type": "User", "created_at": iso_days_ago(100), "public_repos": 1}
        with patch("skillscout.search_skills", return_value=cand), \
             patch("skillscout.fetch_repo", return_value=repo), \
             patch("skillscout.fetch_owner", return_value=owner), \
             patch("skillscout.fetch_tree", return_value=["SKILL.md", "run.sh"]):
            code = skillscout.main(["--no-llm", "sécurité"])
        self.assertEqual(code, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'skillscout' has no attribute 'ask_qwen'`

- [ ] **Step 3: Write minimal implementation**

```python
# à ajouter dans skillscout.py
import argparse
import os
import sys

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "qwen3:8b"
CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "skillscout", "cache.db")

PROMPT = """Tu conseilles un développeur francophone qui cherche un skill d'agent.

Son besoin : {need}

Voici {n} candidats, déjà filtrés sur leur provenance (l'éditeur est fiable ou
le dépôt ne contient aucun code exécutable). Ton travail n'est PAS de juger leur
sûreté — c'est fait — mais de dire lesquels répondent réellement au besoin.

{blocks}

Réponds en français, en trois points numérotés maximum, du plus adapté au moins
adapté. Pour chacun : son identifiant, une phrase sur ce qu'il fait, et surtout
ce qui le distingue des deux autres. Si aucun ne répond au besoin, dis-le
franchement au lieu d'en recommander un par défaut."""


class OllamaError(Exception):
    """Le serveur Ollama local est injoignable ou a répondu en erreur."""


def ask_qwen(need: str, skills: list[dict], model: str = DEFAULT_MODEL) -> str:
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
    }
    req = Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())
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
        owner = c["source"].split("/")[0]
        try:
            repo_meta = fetch_repo(c["source"], cache)
            owner_meta = fetch_owner(owner, cache)
            paths = fetch_tree(c["source"], cache)
        except GhError as e:
            print(f"  ignoré {c['source']} : {e}", file=sys.stderr)
            continue
        row = evaluate(c, repo_meta, owner_meta, paths, now)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Developer/skillscout && python3 -m unittest discover -s tests -v`
Expected: PASS — 41 tests au total

- [ ] **Step 5: Create the launcher and verify end to end**

```bash
mkdir -p ~/bin
cat > ~/bin/skillscout <<'SH'
#!/bin/sh
exec python3 "$HOME/Developer/skillscout/skillscout.py" "$@"
SH
chmod +x ~/bin/skillscout
case ":$PATH:" in *":$HOME/bin:"*) ;; *) echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc ;; esac
```

Run: `~/bin/skillscout --no-llm "tester la sécurité d'un site web"`
Expected: un TOP 10 avec scores, sources et drapeaux ; aucune trace d'erreur `gh`.

Run: `~/bin/skillscout "tester la sécurité d'un site web"`
Expected: le TOP 10, puis trois points numérotés produits par `qwen3:8b`.

- [ ] **Step 6: Commit**

```bash
cd ~/Developer/skillscout
git add skillscout.py tests/test_skillscout.py
git commit -m "feat(cli): synthèse qwen3:8b locale, CLI et lanceur ~/bin"
```
