"""Choix et installation après le top 5 : question, garde-fous, vérification de
l'empreinte, commande « après installation ». Aucun appel réseau ni vrai npx."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skillscout import cli, install
from test_search_v3 import CliCase, NeedJev, cand, inspected


class TestParseChoice(unittest.TestCase):
    def test_numeros_separes_par_virgule_ou_espace(self):
        self.assertEqual(install.parse_choice("1,3", 5), [1, 3])
        self.assertEqual(install.parse_choice(" 3 1 ", 5), [3, 1])
        self.assertEqual(install.parse_choice("2;2,4", 5), [2, 4])

    def test_aucun(self):
        self.assertEqual(install.parse_choice("", 5), [])
        self.assertEqual(install.parse_choice("0", 5), [])

    def test_tout(self):
        self.assertEqual(install.parse_choice("tout", 3), [1, 2, 3])
        self.assertEqual(install.parse_choice("TOUS", 2), [1, 2])

    def test_incompris(self):
        for texte in ("6", "a", "1,x", "-1", "1-3"):
            self.assertIsNone(install.parse_choice(texte, 5), texte)


class TestDemanderChoix(unittest.TestCase):
    def test_redemande_apres_une_reponse_incomprise(self):
        reponses, messages = iter(["9", "2"]), []
        rows = [{"skill_id": "a"}, {"skill_id": "b"}]
        choisis = install.demander_choix(rows, lambda q: next(reponses), messages.append)
        self.assertEqual(choisis, [rows[1]])
        self.assertIn("Réponse non comprise", messages[0])

    def test_fin_de_saisie_vaut_aucun(self):
        def lire(q):
            raise EOFError
        self.assertEqual(install.demander_choix([{"skill_id": "a"}], lire, print), [])


class FauxNpx:
    """Simule `npx skills add/remove` : `add` écrit le SKILL.md fourni dans skills_dir."""
    def __init__(self, skills_dir, contenu=None, code_add=0):
        self.skills_dir, self.contenu, self.code_add = skills_dir, contenu, code_add
        self.appels = []

    def __call__(self, commande, **options):
        self.appels.append(commande)
        if isinstance(commande, str):                     # commande « après installation »
            return subprocess.CompletedProcess(commande, 0)
        action, nom = commande[3], commande[4]
        if action == "add" and self.code_add == 0:
            nom = commande[commande.index("--skill") + 1]
            (self.skills_dir / nom).mkdir(exist_ok=True)
            (self.skills_dir / nom / "SKILL.md").write_text(self.contenu[nom], encoding="utf-8")
        if action == "remove":
            shutil.rmtree(self.skills_dir / nom, ignore_errors=True)
        return subprocess.CompletedProcess(commande, self.code_add if action == "add" else 0)


class InstallCase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.skills = d / "skills"
        self.skills.mkdir()

    def row(self, nom, source="org/repo", installed=None):
        return dict(inspected(cand(nom, source)), installed=installed)

    def installeur(self, npx, installes_npx=(), reponses=()):
        reponses = iter(reponses)
        return install.Installeur(self.skills, "npx", set(installes_npx), npx,
                                  lambda q: next(reponses))


class TestInstalleur(InstallCase):
    def test_installe_et_verifie_l_empreinte(self):
        row = self.row("evals")
        npx = FauxNpx(self.skills, {"evals": row["body"]})
        etat, message = self.installeur(npx).installer(row)
        self.assertEqual(etat, "installe")
        self.assertEqual(npx.appels, [["npx", "--yes", "skills", "add", "org/repo", "--skill",
                                       "evals", "-g", "-a", "claude-code", "-y"]])

    def test_texte_change_depuis_l_inspection_desinstalle(self):
        row = self.row("evals")
        npx = FauxNpx(self.skills, {"evals": "---\nname: evals\n---\n# modifié après coup\n"})
        etat, message = self.installeur(npx).installer(row)
        self.assertEqual(etat, "annule")
        self.assertIn("n'est pas celui inspecté", message)
        self.assertEqual(npx.appels[-1][:5], ["npx", "--yes", "skills", "remove", "evals"])
        self.assertFalse((self.skills / "evals").exists())

    def test_deja_installe_identique_rien_a_faire(self):
        npx = FauxNpx(self.skills)
        etat, _ = self.installeur(npx).installer(self.row("evals", installed="same"))
        self.assertEqual(etat, "a_jour")
        self.assertEqual(npx.appels, [])

    def test_skill_maison_du_meme_nom_jamais_remplace(self):
        (self.skills / "llm-council").mkdir()
        (self.skills / "llm-council" / "SKILL.md").write_text("# le mien\n", encoding="utf-8")
        npx = FauxNpx(self.skills)
        etat, message = self.installeur(npx, installes_npx=()).installer(
            self.row("llm-council", installed="other"))
        self.assertEqual(etat, "refuse")
        self.assertIn("jamais remplacé", message)
        self.assertEqual(npx.appels, [])
        self.assertEqual((self.skills / "llm-council" / "SKILL.md").read_text(encoding="utf-8"),
                         "# le mien\n")

    def test_autre_version_npx_remplacee_seulement_sur_oui(self):
        (self.skills / "evals").mkdir()
        (self.skills / "evals" / "SKILL.md").write_text("# ancienne\n", encoding="utf-8")
        row = self.row("evals", installed="other")
        npx = FauxNpx(self.skills, {"evals": row["body"]})
        etat, _ = self.installeur(npx, {"evals"}, reponses=[""]).installer(row)
        self.assertEqual(etat, "refuse")                  # Entrée = non
        self.assertEqual(npx.appels, [])
        etat, _ = self.installeur(npx, {"evals"}, reponses=["o"]).installer(row)
        self.assertEqual(etat, "installe")

    def test_skill_md_non_lu_refuse(self):
        row = dict(self.row("evals"), skill_md_sha=None)
        npx = FauxNpx(self.skills)
        self.assertEqual(self.installeur(npx).installer(row)[0], "refuse")
        self.assertEqual(npx.appels, [])

    def test_nom_ou_depot_inattendus_jamais_passes_a_npx(self):
        npx = FauxNpx(self.skills)
        for row in (self.row("evals", source="org/repo & calc"), self.row("..")):
            self.assertEqual(self.installeur(npx).installer(row)[0], "echec")
        self.assertEqual(npx.appels, [])

    def test_echec_de_npx(self):
        npx = FauxNpx(self.skills, code_add=1)
        self.assertEqual(self.installeur(npx).installer(self.row("evals"))[0], "echec")


class TestNomsNpx(unittest.TestCase):
    def test_lit_le_registre_et_tolere_son_absence(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.assertEqual(install.noms_npx(home), set())
        (home / ".agents").mkdir()
        (home / ".agents" / ".skill-lock.json").write_text(
            json.dumps({"version": 3, "skills": {"evals": {}, "shadcn": {}}}), encoding="utf-8")
        self.assertEqual(install.noms_npx(home), {"evals", "shadcn"})


class TestInstallerChoix(InstallCase):
    def _lancer(self, rows, reponses, npx, environ=None, which=lambda n: "npx"):
        reponses, messages = iter(reponses), []
        code = install.installer_choix(rows, self.skills, lire=lambda q: next(reponses),
                                       afficher=messages.append, lancer=npx,
                                       environ=environ or {}, which=which, home=self.skills)
        return code, "\n".join(messages)

    def test_aucun_choix_rien_ne_se_passe(self):
        npx = FauxNpx(self.skills)
        self.assertEqual(self._lancer([self.row("a")], [""], npx)[0], 0)
        self.assertEqual(npx.appels, [])

    def test_commande_apres_installation_lancee_une_fois(self):
        rows = [self.row("a"), self.row("b")]
        npx = FauxNpx(self.skills, {"a": rows[0]["body"], "b": rows[1]["body"]})
        code, sortie = self._lancer(rows, ["tout"], npx,
                                    environ={install.ENV_APRES: "py -3 sync.py"})
        self.assertEqual(code, 0)
        self.assertEqual(npx.appels[-1], "py -3 sync.py")
        self.assertEqual(sum(isinstance(a, str) for a in npx.appels), 1)

    def test_sans_variable_rappel_du_catalogue(self):
        row = self.row("a")
        code, sortie = self._lancer([row], ["1"], FauxNpx(self.skills, {"a": row["body"]}))
        self.assertEqual(code, 0)
        self.assertIn(install.ENV_APRES, sortie)

    def test_rien_d_installe_pas_de_commande_apres(self):
        npx = FauxNpx(self.skills)
        code, _ = self._lancer([self.row("a", installed="same")], ["1"], npx,
                               environ={install.ENV_APRES: "py -3 sync.py"})
        self.assertEqual(code, 0)
        self.assertEqual(npx.appels, [])

    def test_commande_apres_en_echec_skills_gardes(self):
        row = self.row("a")
        npx = FauxNpx(self.skills, {"a": row["body"]})

        def lancer(commande, **options):
            if isinstance(commande, str):
                return subprocess.CompletedProcess(commande, 3)
            return npx(commande, **options)
        code, sortie = self._lancer([row], ["1"], lancer,
                                    environ={install.ENV_APRES: "py -3 sync.py"})
        self.assertEqual(code, 1)
        self.assertIn("restent installés", sortie)
        self.assertTrue((self.skills / "a" / "SKILL.md").exists())

    def test_npx_introuvable(self):
        code, sortie = self._lancer([self.row("a")], ["1"], FauxNpx(self.skills),
                                    which=lambda n: None)
        self.assertEqual(code, 1)
        self.assertIn("Node.js", sortie)


class TestCliQuestion(CliCase):
    def test_question_posee_seulement_dans_un_terminal(self):
        cands = [cand("a", "org/a"), cand("b", "org/b", rank_=1)]
        with patch("skillscout.cli._interactif", return_value=False), \
             patch("skillscout.install.installer_choix") as choix:
            code, _, _ = self.run_cli(["x"], cands, NeedJev(lambda d: 3.0))
        self.assertEqual(code, 0)
        choix.assert_not_called()
        with patch("skillscout.cli._interactif", return_value=True), \
             patch("skillscout.install.installer_choix", return_value=0) as choix:
            code, out, _ = self.run_cli(["x"], cands, NeedJev(lambda d: 3.0))
        self.assertEqual(code, 0)
        top, dossier = choix.call_args.args
        self.assertEqual([r["skill_id"] for r in top], ["a", "b"])
        self.assertEqual(dossier, self.skills)
        self.assertIn(" 1. a", out)

    def test_jamais_de_question_en_json(self):
        with patch("skillscout.cli._interactif", return_value=True), \
             patch("skillscout.install.installer_choix") as choix:
            code, _, _ = self.run_cli(["x", "--json"], [cand("a")], NeedJev(lambda d: 3.0))
        self.assertEqual(code, 0)
        choix.assert_not_called()


if __name__ == "__main__":
    unittest.main()
