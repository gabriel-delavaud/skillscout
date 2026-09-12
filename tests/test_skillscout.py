import contextlib, gc, io, json, shutil, subprocess, sys, tempfile, unittest, os
import warnings
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

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
        with contextlib.closing(skillscout.sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE entries SET fetched_at = fetched_at - ?",
                       (8 * 86400,))
        self.assertIsNone(self.cache.get("repo", "a/b"))

    def test_get_put_ne_generent_aucun_resourcewarning(self):
        # Avant le correctif, `with sqlite3.connect(...) as db:` committe mais
        # ne ferme jamais la connexion : un ResourceWarning est émis à la
        # finalisation du garbage collector. `contextlib.closing` la ferme
        # explicitement, donc aucun avertissement ne doit apparaître, même
        # après un passage forcé du GC.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.cache.put("repo", "a/b", {"stars": 1})
            self.cache.get("repo", "a/b")
            gc.collect()
        resource_warnings = [w for w in caught
                             if issubclass(w.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])


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
               "owner": {"type": "Organization", "login": "acme"},
               "default_branch": "main", "full_name": "acme/b"}
        with patch("skillscout.gh_json", return_value=raw):
            out = skillscout.fetch_repo("acme/b", self.cache)
        self.assertEqual(out, {"stars": 42, "pushed_at": "2026-09-01T00:00:00Z",
                               "owner_type": "Organization", "default_branch": "main",
                               "full_name": "acme/b", "owner_login": "acme"})

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

    def test_fetch_tree_truncated_signale_la_troncature(self):
        raw = {"tree": [{"path": "SKILL.md"}], "truncated": True}
        with patch("skillscout.gh_json", return_value=raw):
            self.assertTrue(skillscout.fetch_tree_truncated("a/b", self.cache))

    def test_fetch_tree_truncated_faux_par_defaut(self):
        raw = {"tree": [{"path": "SKILL.md"}]}
        with patch("skillscout.gh_json", return_value=raw):
            self.assertFalse(skillscout.fetch_tree_truncated("a/b", self.cache))

    def test_fetch_tree_et_fetch_tree_truncated_partagent_le_cache(self):
        raw = {"tree": [{"path": "SKILL.md"}], "truncated": True}
        with patch("skillscout.gh_json", return_value=raw) as g:
            skillscout.fetch_tree("a/b", self.cache)
            skillscout.fetch_tree_truncated("a/b", self.cache)
        self.assertEqual(g.call_count, 1)


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

    def test_detecte_les_nouvelles_extensions(self):
        paths = ["run.bash", "deploy.zsh", "notebook.ipynb", "main.go",
                 "lib.rs", "index.php"]
        self.assertEqual(skillscout.find_executables(paths), paths)

    def test_detecte_le_repertoire_bin(self):
        self.assertEqual(skillscout.find_executables(["bin/tool.txt"]),
                         ["bin/tool.txt"])


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

    def test_drapeau_organisation_est_factuel_pas_un_verdict(self):
        # F4 : « org vérifiée » prétendait à une vérification qui n'a jamais
        # eu lieu ; le nouveau libellé ne doit énoncer que les seuils mesurés.
        out = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                  ["SKILL.md"], NOW)
        self.assertNotIn("org vérifiée", out["flags"])
        self.assertIn(
            f"organisation : ≥{skillscout.MIN_OWNER_AGE_DAYS} j, "
            f"≥{skillscout.MIN_PUBLIC_REPOS} dépôts publics, dépôt actif",
            out["flags"],
        )

    def test_drapeau_singulier_pour_un_seul_fichier_executable(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                  ["SKILL.md", "a.sh"], NOW)
        self.assertIn("⚠ 1 fichier exécutable", out["flags"])
        self.assertNotIn("⚠ 1 fichiers exécutables", out["flags"])

    def test_redirection_de_depot_est_exclue(self):
        # F1 : `source` vient de skills.sh et peut être périmé si le dépôt a
        # été transféré depuis. `gh api repos/{source}` suit la redirection
        # sans erreur, donc `repo_meta` décrit alors un AUTRE dépôt — il faut
        # le détecter via `full_name` plutôt que de faire confiance à `source`.
        cand = dict(self.CAND, source="vercel/foo")
        repo_redirige = dict(self.REPO, full_name="attacker/foo",
                             owner_login="attacker")
        out = skillscout.evaluate(cand, repo_redirige, self.ORG_OK,
                                  ["SKILL.md"], NOW)
        self.assertTrue(out["excluded"])
        self.assertIn("redirige", out["reason"])

    def test_owner_vient_de_repo_meta_pas_de_source(self):
        # Même sans redirection détectée (full_name absent ici), l'éditeur
        # utilisé pour le score/la confiance doit venir de `owner_login`.
        cand = dict(self.CAND, source="vercel/foo")
        repo = dict(self.REPO, owner_login="pasvercel")
        out = skillscout.evaluate(cand, repo, self.USER, ["SKILL.md"], NOW)
        self.assertNotIn("éditeur en liste blanche", out["flags"])

    def test_arbre_tronque_exclut_si_editeur_non_confiance(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.USER,
                                  ["SKILL.md"], NOW, truncated=True)
        self.assertTrue(out["excluded"])
        self.assertIn("tronqu", out["reason"])

    def test_arbre_tronque_toleree_si_editeur_confiance(self):
        out = skillscout.evaluate(self.CAND, self.REPO, self.ORG_OK,
                                  ["SKILL.md"], NOW, truncated=True)
        self.assertFalse(out["excluded"])


class TestRank(unittest.TestCase):
    def test_ecarte_les_exclus_trie_et_tronque(self):
        rows = [
            {"skill_id": "a", "excluded": False, "score": 10.0},
            {"skill_id": "b", "excluded": True, "score": 99.0},
            {"skill_id": "c", "excluded": False, "score": 50.0},
        ]
        out = skillscout.rank(rows, top=2)
        self.assertEqual([r["skill_id"] for r in out], ["c", "a"])


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

    def _dix_candidats(self):
        return [{"skill_id": f"s{i}", "source": f"o{i}/r", "score": float(i),
                 "flags": [], "body": f"corps {i}"} for i in range(10)]

    def _capture_payload(self, skills, need="besoin"):
        cm = MagicMock()
        cm.read.return_value = json.dumps({"message": {"content": "ok"}}).encode()
        cm.__enter__ = lambda s: cm
        cm.__exit__ = lambda s, *a: False
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode())
            return cm

        with patch("skillscout.urlopen", side_effect=fake_urlopen):
            skillscout.ask_qwen(need, skills, model="qwen3:8b")
        return captured["payload"]

    def test_seuls_les_cinq_premiers_candidats_sont_envoyes(self):
        payload = self._capture_payload(self._dix_candidats())
        content = payload["messages"][0]["content"]
        for i in range(skillscout.TOP_N_FOR_LLM):
            self.assertIn(f"s{i}", content)
        for i in range(skillscout.TOP_N_FOR_LLM, 10):
            self.assertNotIn(f"s{i}", content)

    def test_num_ctx_est_precise_dans_les_options(self):
        payload = self._capture_payload(self._dix_candidats())
        self.assertIn("options", payload)
        self.assertEqual(payload["options"]["num_ctx"], skillscout.NUM_CTX)
        self.assertGreater(payload["options"]["num_ctx"], 4096)

    def test_le_prompt_accorde_le_nombre_de_points_au_nombre_recu(self):
        deux = [{"skill_id": "a", "source": "x/y", "score": 1.0,
                 "flags": [], "body": "x"},
                {"skill_id": "b", "source": "x/z", "score": 1.0,
                 "flags": [], "body": "y"}]
        payload = self._capture_payload(deux)
        content = payload["messages"][0]["content"]
        self.assertNotIn("deux autres", content)
        self.assertNotIn("trois points", content)
        self.assertIn("2 candidats", content)
        self.assertIn("2 points numérotés maximum", content)

    def test_message_modele_introuvable_suggere_pull(self):
        err = HTTPError("http://localhost", 404, "Not Found", None, None)
        try:
            with patch("skillscout.urlopen", side_effect=err):
                with self.assertRaises(skillscout.OllamaError) as ctx:
                    skillscout.ask_qwen("sécurité", self.ROWS)
            msg = str(ctx.exception)
            self.assertIn("ollama pull", msg)
            self.assertNotIn("ollama serve", msg)
        finally:
            err.close()  # évite un ResourceWarning différé à la finalisation

    def test_message_serveur_injoignable_suggere_serve(self):
        with patch("skillscout.urlopen", side_effect=OSError("refusé")):
            with self.assertRaises(skillscout.OllamaError) as ctx:
                skillscout.ask_qwen("sécurité", self.ROWS)
        msg = str(ctx.exception)
        self.assertIn("ollama serve", msg)
        self.assertNotIn("ollama pull", msg)


class TestFormatTop10(unittest.TestCase):
    def test_affiche_score_source_et_drapeaux(self):
        out = skillscout.format_top10([
            {"skill_id": "a", "source": "x/y", "score": 61.5,
             "flags": ["markdown pur"], "installs": 12}])
        self.assertIn("x/y", out)
        self.assertIn("61.5", out)
        self.assertIn("markdown pur", out)


class TestMain(unittest.TestCase):
    # Ruling du contrôleur : CACHE_PATH ne doit jamais pointer vers le vrai
    # ~/.cache/skillscout/cache.db pendant les tests. On isole chaque test
    # dans un répertoire tempfile, jamais dans le home de l'utilisateur.
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)
        patcher = patch("skillscout.CACHE_PATH",
                        os.path.join(self.cache_dir, "cache.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        # F5 : `main()` imprime sur stdout/stderr — on capture pour que la
        # sortie de `python3 -m unittest` reste propre.
        self._redirect_stdout = contextlib.redirect_stdout(io.StringIO())
        self._redirect_stdout.__enter__()
        self.addCleanup(self._redirect_stdout.__exit__, None, None, None)
        self._redirect_stderr = contextlib.redirect_stderr(io.StringIO())
        self._redirect_stderr.__enter__()
        self.addCleanup(self._redirect_stderr.__exit__, None, None, None)

    def test_no_llm_court_circuite_ollama(self):
        cand = [{"skill_id": "s", "name": "s", "source": "etalab-ia/skills", "installs": 13}]
        repo = {"stars": 18, "pushed_at": iso_days_ago(1),
                "owner_type": "Organization", "default_branch": "main"}
        owner = {"type": "Organization", "created_at": iso_days_ago(2000), "public_repos": 73}
        with patch("skillscout.search_skills", return_value=cand), \
             patch("skillscout.fetch_repo", return_value=repo), \
             patch("skillscout.fetch_owner", return_value=owner), \
             patch("skillscout.fetch_tree", return_value=["skills/s/SKILL.md"]), \
             patch("skillscout.fetch_tree_truncated", return_value=False), \
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
             patch("skillscout.fetch_tree", return_value=["SKILL.md", "run.sh"]), \
             patch("skillscout.fetch_tree_truncated", return_value=False):
            code = skillscout.main(["--no-llm", "sécurité"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
