import json, subprocess, sys, tempfile, unittest, os
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

    def test_organisation_sans_created_at_echoue(self):
        # Absence de created_at doit échouer le seuil d'âge, même avec repos/freshness OK.
        meta = {"type": "Organization", "created_at": "", "public_repos": 73}
        self.assertFalse(skillscout.is_trusted_publisher("inconnue", meta, self.FRESH, NOW))
        # Même chose avec un timestamp illisible
        meta2 = {"type": "Organization", "created_at": "invalid", "public_repos": 73}
        self.assertFalse(skillscout.is_trusted_publisher("inconnue", meta2, self.FRESH, NOW))


if __name__ == "__main__":
    unittest.main()
