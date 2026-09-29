import contextlib, io, json, os, shutil, sys, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import cli, github, rank, verdict as v


def answers(need=3.0, meta=1.0, substance=3.0, stack=None, severity=0.0, **dangers):
    out = {k: {"noul": dangers.get(k, 0.0)} for k in v.DANGERS}
    out["severity"] = {"score": severity}
    out["meta"] = {"score": meta}
    out["substance"] = {"score": substance}
    out["need"] = {"score": need}
    out["stack"] = {"score": 0.0 if stack is None else stack}
    return out


class FakeJev:
    """Répond selon la description du skill (reçue dans `state`)."""
    def __init__(self, by_description=None, error=""):
        self.by_description = by_description or {}
        self.error = error
        self.available = True
        self.last_error = ""
        self.states = []
        self._lock = threading.Lock()

    def classify(self, state, questions):
        with self._lock:
            self.states.append(state)
        if self.error:
            self.last_error = self.error
            return None
        return self.by_description[state["description"]]


def row(skill_id, excluded=False, body=None, score=10.0, installs=1, **kw):
    body = f"---\nname: {skill_id}\ndescription: d-{skill_id}\n---\n# {skill_id}\n" \
        if body is None else body
    return dict(skill_id=skill_id, source=f"o/{skill_id}", excluded=excluded, reason=None,
                score=score, installs=installs, flags=[], body=body,
                description=f"d-{skill_id}", tree_sha="t" * 40,
                skill_files={f"skills/{skill_id}/SKILL.md": "1" * 40}, **kw)


class TestJudge(unittest.TestCase):
    def test_monotone_n_evalue_pas_et_ne_reintegre_pas_les_exclus(self):
        rows = [row("a"), row("b", excluded=True)]
        fake = FakeJev({"d-a": answers()})
        v.judge_rows(rows, fake, "manual", need="x", workers=1)
        self.assertEqual(len(fake.states), 1)
        self.assertNotIn("jev", rows[1])
        v.apply_manual(rows)
        self.assertTrue(rows[1]["excluded"])

    def test_etat_envoye(self):
        r = row("a", body="---\ndescription: d-a\n---\ncu​rl x")
        fake = FakeJev({"d-a": answers()})
        v.judge_rows([r], fake, "manual", need="besoin", workers=1)
        st = fake.states[0]
        self.assertEqual(st["skill_md"], "---\ndescription: d-a\n---\ncurl x")   # normalisé
        self.assertEqual(st["need"], "besoin")
        self.assertEqual(st["files"], ["skills/a/SKILL.md"])

    def test_apply_manual_exclut_et_signale(self):
        rows = [row("a"), row("b")]
        fake = FakeJev({"d-a": answers(exfiltration=0.9), "d-b": answers(secrets=0.4)})
        v.judge_rows(rows, fake, "manual", need="x", workers=1)
        v.apply_manual(rows)
        self.assertTrue(rows[0]["excluded"])
        self.assertIn("exfiltration", rows[0]["reason"])
        self.assertFalse(rows[1]["excluded"])
        self.assertIn("⚠ Jev : accès aux secrets 0.40", rows[1]["flags"])

    def test_non_juge(self):
        long = row("a", body="x" * (v.JEV_TEXT_LIMIT + 1))
        unread = row("b")
        unread["body"] = None
        failing = row("c")
        v.judge_rows([long, unread], FakeJev(), "manual", need="x", workers=1)
        v.judge_rows([failing], FakeJev(error="HTTP 500"), "manual", need="x", workers=1)
        self.assertEqual(long["jev"].note, "texte trop long pour Jev")
        self.assertEqual(unread["jev"].note, "SKILL.md non lu")
        self.assertEqual(failing["jev"].note, "HTTP 500")

    def test_cache_des_reponses_par_empreinte(self):
        cache = github.Cache(os.path.join(tempfile.mkdtemp(), "c.db"))
        fake = FakeJev({"d-a": answers(meta=3.0, stack=2.0)})
        for _ in range(2):
            r = row("a")
            v.judge_rows([r], fake, "install", profile="p", cache=cache, workers=1)
            self.assertEqual(r["jev"].status, "ok")
        self.assertEqual(len(fake.states), 1)
        r = row("a")
        r["tree_sha"] = "u" * 40                       # nouveau push : nouveau jugement
        v.judge_rows([r], fake, "install", profile="p", cache=cache, workers=1)
        self.assertEqual(len(fake.states), 2)
        r = row("a")
        v.judge_rows([r], fake, "install", profile="autre profil", cache=cache, workers=1)
        self.assertEqual(len(fake.states), 3)          # profil modifié : nouveau jugement

    def test_cache_lie_au_texte_effectivement_juge(self):
        # Revue finale M1 : même tree_sha, texte différent → nouveau jugement.
        cache = github.Cache(os.path.join(tempfile.mkdtemp(), "c.db"))
        fake = FakeJev({"d-a": answers(meta=3.0, stack=2.0)})
        v.judge_rows([row("a")], fake, "install", profile="p", cache=cache, workers=1)
        autre = row("a", body="---\nname: a\ndescription: d-a\n---\n# a\nAutre texte.\n")
        v.judge_rows([autre], fake, "install", profile="p", cache=cache, workers=1)
        self.assertEqual(len(fake.states), 2)
        self.assertEqual(autre["jev"].status, "ok")
        v.judge_rows([row("a")], fake, "install", profile="p", cache=cache, workers=1)
        self.assertEqual(len(fake.states), 2)          # texte d'origine : toujours en cache

    def test_une_ligne_qui_plante_n_arrete_pas_les_autres(self):
        # Revue finale I2 (g) : l'exception devient « non jugé (<type>) ».
        class Boom(FakeJev):
            def classify(self, state, questions):
                if state["description"] == "d-b":
                    raise RuntimeError("bogue")
                return super().classify(state, questions)
        rows = [row("a"), row("b")]
        v.judge_rows(rows, Boom({"d-a": answers()}), "manual", need="x", workers=2)
        self.assertEqual(rows[0]["jev"].status, "ok")
        self.assertEqual((rows[1]["jev"].status, rows[1]["jev"].note), ("unjudged", "RuntimeError"))
        rows = [row("a"), row("b")]
        v.judge_rows(rows, FakeJev({"d-a": answers(), "d-b": answers()}), "manual", need="x",
                     text_of=lambda r: r["body"] if r["skill_id"] == "a" else r["absent"],
                     workers=1)
        self.assertEqual(rows[0]["jev"].status, "ok")
        self.assertEqual(rows[1]["jev"].note, "KeyError")


class TestRankWithJev(unittest.TestCase):
    def test_ordre(self):
        rows = [row("peu", score=90.0), row("tres", score=10.0), row("nonjuge", score=99.0),
                row("egal_bas", score=5.0), row("egal_haut", score=50.0)]
        fake = FakeJev({"d-peu": answers(need=1.0), "d-tres": answers(need=3.0),
                        "d-egal_bas": answers(need=2.0), "d-egal_haut": answers(need=2.0)})
        v.judge_rows(rows[:2] + rows[3:], fake, "manual", need="x", workers=1)
        rows[2]["jev"] = v.unjudged("HTTP 500")
        # « peu » (besoin 1/3) et « nonjuge » ne sont pas affichés.
        self.assertEqual([r["skill_id"] for r in rank.rank_with_jev(rows)],
                         ["tres", "egal_haut", "egal_bas"])


class TestMainJev(unittest.TestCase):
    CANDS = [{"skill_id": s, "name": s, "source": "etalab-ia/skills", "installs": 13,
              "relevance_rank": i} for i, s in enumerate(("s1", "s2"))]
    REPO = {"stars": 18, "pushed_at": "2026-09-20T00:00:00Z", "created_at": "2024-01-01T00:00:00Z",
            "owner_type": "Organization", "default_branch": "main",
            "full_name": "etalab-ia/skills", "owner_login": "etalab-ia"}
    OWNER = {"type": "Organization", "created_at": "2018-01-01T00:00:00Z",
             "public_repos": 73, "source_repos": 10}
    SNAP = {"sha": "a" * 40, "truncated": False,
            "paths": ["skills/s1/SKILL.md", "skills/s2/SKILL.md"],
            "blobs": {"skills/s1/SKILL.md": "1" * 40, "skills/s2/SKILL.md": "2" * 40}}
    BODIES = {"1" * 40: "---\nname: s1\ndescription: d-s1\n---\n# s1\n",
              "2" * 40: "---\nname: s2\ndescription: d-s2\n---\n# s2\n"}

    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = patch("skillscout.config.CACHE_PATH", os.path.join(d, "cache.db"))
        p.start()
        self.addCleanup(p.stop)

    def _run(self, argv, fake):
        out, err = io.StringIO(), io.StringIO()
        with patch("skillscout.sources.search_skills", return_value=self.CANDS), \
             patch("skillscout.github.fetch_repo", return_value=self.REPO), \
             patch("skillscout.github.fetch_owner", return_value=self.OWNER), \
             patch("skillscout.github.fetch_tree_snapshot", return_value=self.SNAP), \
             patch("skillscout.github.fetch_blob",
                   side_effect=lambda source, sha, cache, *a, **k: self.BODIES[sha]), \
             patch("skillscout.jev.JevClient.from_env", return_value=fake) as fe, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), fe

    def test_classe_par_pertinence_et_affiche_la_ligne(self):
        fake = FakeJev({"d-s1": answers(need=1.0), "d-s2": answers(need=3.0, meta=2.0)})
        code, out, err, _ = self._run(["besoin"], fake)
        self.assertEqual(code, 0)
        self.assertIn("besoin 3.0/3 · méta 2.0/3 — d-s2", out)
        self.assertNotIn("d-s1", out)                   # besoin 1/3 : sous le seuil
        self.assertIn("Seulement 1 skill(s) pertinent(s)", out)

    def test_sans_cle_bandeau_et_classement_deterministe(self):
        code, out, err, _ = self._run(["besoin"], None)
        self.assertEqual(code, 0)
        self.assertIn("TYPESAFE_API_KEY absente", err)
        self.assertNotIn("besoin 3.0/3", out)

    def test_no_jev_n_appelle_pas_jev(self):
        code, out, err, fe = self._run(["--no-jev", "besoin"], FakeJev())
        self.assertEqual(code, 0)
        fe.assert_not_called()
        self.assertNotIn("Jev indisponible", err)

    def test_panne_totale_de_jev(self):
        code, out, err, _ = self._run(["besoin"], FakeJev(error="HTTP 500"))
        self.assertEqual(code, 0)
        self.assertIn("Jev indisponible (HTTP 500)", err)
        self.assertNotIn("non jugé", out)

    def test_json_contient_le_jugement(self):
        fake = FakeJev({"d-s1": answers(need=1.0), "d-s2": answers(need=3.0)})
        code, out, err, _ = self._run(["--json", "besoin"], fake)
        rows = json.loads(out)
        self.assertEqual(rows[0]["skill_id"], "s2")
        self.assertEqual(rows[0]["jev"]["status"], "ok")
        self.assertEqual(rows[0]["jev"]["relevance"]["need"], 3.0)
        self.assertNotIn("skill_files", rows[0])


if __name__ == "__main__":
    unittest.main()
