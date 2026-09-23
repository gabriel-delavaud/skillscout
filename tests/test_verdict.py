import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import verdict as v


def answers(mode="install", severity=0.0, meta=3.0, stack=0.0, need=3.0,
            substance=3.0, **dangers):
    out = {k: {"noul": dangers.get(k, 0.0)} for k in v.DANGERS}
    out["severity"] = {"score": severity}
    out["meta"] = {"score": meta}
    out["substance"] = {"score": substance}
    out["stack" if mode == "install" else "need"] = {"score": stack if mode == "install" else need}
    return out


def judged(mode="install", **kw):
    return v.parse_answers(answers(mode, **kw), mode)


class TestQuestions(unittest.TestCase):
    def test_jeux_de_questions(self):
        common = set(v.DANGERS) | {"severity", "meta", "substance"}
        self.assertEqual(set(v.questions_for("manual")), common | {"need"})
        self.assertEqual(set(v.questions_for("install")), common | {"stack"})
        with self.assertRaises(ValueError):
            v.questions_for("autre")

    def test_types_et_texte_designe_comme_donnee(self):
        qs = v.questions_for("install")
        for k in v.DANGERS:
            self.assertEqual(qs[k]["type"], "noul")
        for k in ("severity", "meta", "stack", "substance"):
            self.assertEqual(qs[k]["type"], "score")
            self.assertEqual(len(qs[k]["criteria"]), 4)
        for q in qs.values():
            self.assertIn("`skill_md`", q["instructions"])
            self.assertIn("DATA", q["instructions"])

    def test_build_state(self):
        s = v.build_state("texte", "desc", ["a/SKILL.md"], need="besoin")
        self.assertEqual(s, {"skill_md": "texte", "description": "desc",
                             "files": ["a/SKILL.md"], "need": "besoin"})
        s = v.build_state("t", "", [], profile="profil")
        self.assertEqual(s["profile"], "profil")
        self.assertNotIn("need", s)


class TestParseAnswers(unittest.TestCase):
    def test_reponse_complete(self):
        j = judged(severity=1.5, meta=2.0, stack=1.0, secrets=0.4)
        self.assertEqual(j.status, "ok")
        self.assertEqual(j.dangers["secrets"], 0.4)
        self.assertEqual(j.severity, 1.5)
        self.assertEqual(j.relevance, {"meta": 2.0, "stack": 1.0, "substance": 3.0})

    def test_absence_de_reponse(self):
        j = v.parse_answers(None, "manual")
        self.assertEqual((j.status, j.note), ("unjudged", "Jev indisponible"))

    def test_cle_manquante_est_une_panne_pas_un_zero(self):
        a = answers()
        del a["persistence"]
        self.assertEqual(v.parse_answers(a, "install").status, "unjudged")
        a = answers()
        a["meta"] = {"noul": 0.1}          # mauvais type de réponse
        self.assertEqual(v.parse_answers(a, "install").status, "unjudged")

    def test_valeurs_hors_bornes_ou_non_numeriques(self):
        for bad in (1.5, -0.1, "x", None, float("nan")):
            a = answers()
            a["secrets"] = {"noul": bad}
            self.assertEqual(v.parse_answers(a, "install").status, "unjudged", bad)
        a = answers()
        a["severity"] = {"score": 3.5}
        self.assertEqual(v.parse_answers(a, "install").status, "unjudged")

    def test_mode_manuel_exige_need(self):
        a = answers("install")
        self.assertEqual(v.parse_answers(a, "manual").status, "unjudged")


class TestManualVerdict(unittest.TestCase):
    def test_bornes_d_exclusion(self):
        self.assertIsNone(v.manual_verdict(judged("manual", exfiltration=0.849))[0])
        self.assertIsNotNone(v.manual_verdict(judged("manual", exfiltration=0.85))[0])
        self.assertIsNone(v.manual_verdict(judged("manual", destruction=0.70, severity=1.99))[0])
        reason, _ = v.manual_verdict(judged("manual", destruction=0.70, severity=2.0))
        self.assertIn("destruction", reason)
        self.assertIn("0.70", reason)

    def test_tous_les_dangers_comptent(self):
        for k in v.DANGERS:
            self.assertIsNotNone(v.manual_verdict(judged("manual", **{k: 0.9}))[0], k)

    def test_drapeaux(self):
        self.assertEqual(v.manual_verdict(judged("manual", secrets=0.349)), (None, []))
        reason, flags = v.manual_verdict(judged("manual", secrets=0.35))
        self.assertIsNone(reason)
        self.assertEqual(flags, ["⚠ Jev : accès aux secrets 0.35"])

    def test_non_juge(self):
        reason, flags = v.manual_verdict(v.unjudged("Jev indisponible"))
        self.assertIsNone(reason)
        self.assertEqual(flags, ["⚠ non jugé par Jev (Jev indisponible)"])


class TestInstallVerdict(unittest.TestCase):
    def test_installable(self):
        self.assertIsNone(v.install_verdict(judged(meta=2.0, substance=2.0)))
        self.assertIsNone(v.install_verdict(judged(meta=0.0, stack=2.0, substance=2.0)))

    def test_bornes_de_danger_et_gravite(self):
        self.assertIsNone(v.install_verdict(judged(manipulation=0.199)))
        self.assertIn("manipulation", v.install_verdict(judged(manipulation=0.20)))
        self.assertIsNone(v.install_verdict(judged(severity=0.99)))
        self.assertIn("gravité", v.install_verdict(judged(severity=1.0)))

    def test_pertinence_et_substance(self):
        self.assertIn("pertinent", v.install_verdict(judged(meta=1.99, stack=1.99)))
        self.assertIn("substance", v.install_verdict(judged(substance=1.99)))

    def test_non_juge_jamais_installable(self):
        self.assertIn("non jugé", v.install_verdict(v.unjudged("réponse Jev incomplète")))


class TestRelevanceLine(unittest.TestCase):
    def test_lignes(self):
        self.assertEqual(v.relevance_line(judged("manual", need=3.0, meta=2.0), "manual"),
                         "besoin 3.0/3 · méta 2.0/3")
        self.assertEqual(v.relevance_line(judged(meta=3.0, stack=1.0), "install"),
                         "méta 3.0/3 · pile 1.0/3")
        self.assertEqual(v.relevance_line(v.unjudged("x"), "manual"), "")


if __name__ == "__main__":
    unittest.main()
