"""Recherche manuelle : requêtes multiples, ordre de pertinence, lots Jev,
copies regroupées, skills déjà installés, cas de référence hors ligne."""
import contextlib, io, json, os, shutil, sys, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import cli, config, explain, github, local, rank, sources, verdict as v

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "search_evals"


def answers(need, meta=1.0, substance=3.0):
    out = {k: {"noul": 0.0} for k in v.DANGERS}
    out.update(severity={"score": 0.0}, meta={"score": meta}, substance={"score": substance},
               need={"score": need})
    return out


class NeedJev:
    """Jev factice : la pertinence au besoin est calculée depuis la description."""
    def __init__(self, need_of, explain=None):
        self.need_of = need_of
        self.explain = explain          # description -> réponses d'explication, ou None
        self.last_error = ""
        self.calls = 0
        self.explain_calls = []
        self._lock = threading.Lock()

    def classify(self, state, questions):
        if "writing" in questions:
            with self._lock:
                self.explain_calls.append(state["description"])
            return self.explain(state["description"]) if self.explain else None
        with self._lock:
            self.calls += 1
        return answers(self.need_of(state["description"]))


def cand(skill_id, source="org/repo", installs=1, rank_=0):
    return {"skill_id": skill_id, "name": skill_id, "source": source, "installs": installs,
            "relevance_rank": rank_}


def inspected(c, *, excluded=False, score=80.0, content=None):
    """Ligne telle que la rendrait `inspection.inspect_candidate`."""
    body = content or f"---\nname: {c['skill_id']}\ndescription: {c['skill_id']}\n---\n# x\n"
    return dict(c, excluded=excluded, reason="écarté" if excluded else None, score=score,
                flags=[], body=body, description=c["skill_id"], md_name=c["skill_id"],
                skill_files={"SKILL.md": "1" * 40}, tree_sha="t" * 40,
                skill_md_sha=github.git_blob_sha(body.encode()))


class TestSearchMany(unittest.TestCase):
    def test_fusionne_au_meilleur_rang_puis_installs(self):
        results = {"a": [cand("x", rank_=0), cand("y", installs=5, rank_=1)],
                   "b": [cand("z", installs=9, rank_=0), cand("X", rank_=3), cand("y", installs=5, rank_=0)]}
        with patch("skillscout.sources.search_skills", side_effect=lambda q: results[q]):
            out, failures = sources.search_many(["a", "b"])
        self.assertEqual(failures, [])
        self.assertEqual([(c["skill_id"], c["relevance_rank"]) for c in out],
                         [("z", 0), ("y", 0), ("x", 0)])   # rang 0 partout : installs départagent

    def test_une_requete_en_panne_n_arrete_pas_les_autres(self):
        def search(q):
            if q == "hs":
                raise sources.SearchError("hors ligne")
            return [cand("x")]
        with patch("skillscout.sources.search_skills", side_effect=search):
            out, failures = sources.search_many(["hs", "ok"])
        self.assertEqual([c["skill_id"] for c in out], ["x"])
        self.assertEqual(len(failures), 1)
        self.assertIn("hors ligne", failures[0])

    def test_toutes_en_panne_leve(self):
        with patch("skillscout.sources.search_skills",
                   side_effect=sources.SearchError("hors ligne")):
            with self.assertRaises(sources.SearchError):
                sources.search_many(["a", "b"])


class ScriptedJev:
    """Jev factice : réponses par description ; None simule une panne (HTTP 429)."""
    def __init__(self, by_description, fail_after=None):
        self.by_description = by_description
        self.fail_after = fail_after
        self.last_error = ""
        self.calls = 0
        self._lock = threading.Lock()

    def classify(self, state, questions):
        if "writing" in questions:
            return None
        with self._lock:
            self.calls += 1
            calls = self.calls
        if self.fail_after is not None and calls > self.fail_after:
            self.last_error = "HTTP 429"
            return None
        return self.by_description(state["description"])


def described(c, desc, **kw):
    """Ligne inspectée dont la description (et donc la réponse de Jev) est `desc`."""
    body = f"---\nname: {c['skill_id']}\ndescription: {desc}\n---\n# {c['source']}\n"
    return dict(inspected(c, content=body, **kw), description=desc)


class TestCopies(unittest.TestCase):
    def test_meme_nom_meme_contenu_regroupes_la_plus_sure_reste(self):
        rows = []
        new = [inspected(cand("s", "fork/a", rank_=0), score=40.0),
               inspected(cand("s", "orig/b", rank_=5), score=90.0),
               inspected(cand("t", "orig/b", rank_=1))]
        added = rank.add_deduplicated(rows, new)
        self.assertEqual(len(added), 2)
        kept = next(r for r in rows if r["skill_id"] == "s")
        rank.describe_group(kept)
        self.assertEqual(kept["source"], "orig/b")
        self.assertEqual(kept["copies"], ["fork/a"])
        self.assertEqual(kept["group_rank"], 0)          # meilleur rang du groupe…
        self.assertEqual(kept["relevance_rank"], 5)      # … sans écraser le sien

    def test_meme_nom_contenu_different_variante(self):
        # Forks retouchés et traductions : une seule ligne, les autres en variantes.
        rows = []
        added = rank.add_deduplicated(rows, [inspected(cand("s", "a/a"), score=90.0),
                                             inspected(cand("s", "b/b"), content="# autre\n"),
                                             inspected(cand("s", "c/c"))])
        self.assertEqual(len(rows), 1)
        self.assertEqual(added, rows)
        rank.describe_group(rows[0])
        self.assertEqual(rows[0]["source"], "a/a")
        self.assertEqual(rows[0]["variants"], ["b/b"])
        self.assertEqual(rows[0]["copies"], ["c/c"])

    def test_noms_differents_non_regroupes(self):
        rows = []
        rank.add_deduplicated(rows, [inspected(cand("s", "a/a")), inspected(cand("t", "a/a"))])
        self.assertEqual(len(rows), 2)

    def test_versions_ecartees_jamais_presentees_comme_alternatives(self):
        # Revue : un fork écarté restait affiché « +1 copie ».
        rows = []
        rank.add_deduplicated(rows, [inspected(cand("s", "perso/a"), excluded=True),
                                     inspected(cand("s", "perso/b"), excluded=True,
                                               content="# autre\n")])
        rank.add_deduplicated(rows, [inspected(cand("s", "org/c"))])
        rank.describe_group(rows[0])
        self.assertEqual(rows[0]["source"], "org/c")
        self.assertEqual((rows[0]["copies"], rows[0]["variants"]), ([], []))
        self.assertEqual(sorted(v["source"] for v in rows[0]["excluded_versions"]),
                         ["perso/a", "perso/b"])
        self.assertEqual(sum(1 for r in rank.members(rows) if r["excluded"]), 2)

    def test_copie_d_un_lot_suivant_pas_rejugee(self):
        rows = []
        rank.add_deduplicated(rows, [inspected(cand("s", "a/a"))])
        rows[0]["jev"] = "déjà jugé"
        added = rank.add_deduplicated(rows, [inspected(cand("s", "b/b"))])
        self.assertEqual(added, [])
        rank.describe_group(rows[0])
        self.assertEqual(rows[0]["copies"], ["b/b"])

    def test_copie_sure_remplace_une_copie_ecartee(self):
        rows = []
        rank.add_deduplicated(rows, [inspected(cand("s", "perso/a"), excluded=True)])
        added = rank.add_deduplicated(rows, [inspected(cand("s", "org/b"))])
        self.assertEqual([r["source"] for r in rows], ["org/b"])
        self.assertEqual(added, rows)
        rank.describe_group(rows[0])
        self.assertEqual(rows[0]["excluded_versions"][0]["source"], "perso/a")


class TestPromotion(unittest.TestCase):
    def _judged(self, row, need, excluded=False):
        row["jev"] = v.Judgement("ok", {k: 0.0 for k in v.DANGERS}, 0.0,
                                 {"need": need, "meta": 1.0, "substance": 3.0})
        row["excluded"] = excluded
        return row

    def test_version_rejetee_remplacee_par_la_suivante(self):
        rows = []
        rank.add_deduplicated(rows, [described(cand("s", "gros/a"), "a", score=95.0),
                                     described(cand("s", "petit/b"), "b", score=40.0)])
        self._judged(rows[0], 0.0)                       # jugée hors sujet
        promoted = rank.promote(rows)
        self.assertEqual([r["source"] for r in promoted], ["petit/b"])
        self.assertIs(rows[0], promoted[0])

    def test_copie_identique_d_une_version_jugee_pas_promue(self):
        rows = []
        rank.add_deduplicated(rows, [inspected(cand("s", "gros/a"), score=95.0),
                                     inspected(cand("s", "copie/b"), score=40.0)])
        self._judged(rows[0], 0.0)
        self.assertEqual(rank.promote(rows), [])

    def test_au_plus_max_promotions_par_nom(self):
        rows = []
        rank.add_deduplicated(rows, [described(cand("s", f"o/{i}"), f"d{i}", score=90.0 - i)
                                     for i in range(5)])
        promoted = 0
        while True:
            for r in rows:
                if "jev" not in r:
                    self._judged(r, 0.0)
            batch = rank.promote(rows)
            if not batch:
                break
            promoted += len(batch)
        self.assertEqual(promoted, rank.MAX_PROMOTIONS)

    def test_ligne_pertinente_ou_non_jugee_pas_remplacee(self):
        rows = []
        rank.add_deduplicated(rows, [described(cand("s", "a/a"), "a", score=90.0),
                                     described(cand("s", "b/b"), "b")])
        self._judged(rows[0], 3.0)
        self.assertEqual(rank.promote(rows), [])
        rows[0]["jev"] = v.unjudged("HTTP 500")
        self.assertEqual(rank.promote(rows), [])


class TestLocalStatus(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _put(self, name, body):
        (self.dir / name).mkdir()
        (self.dir / name / "SKILL.md").write_bytes(body.encode())

    def test_meme_empreinte_deja_installe(self):
        row = inspected(cand("s"))
        self._put("s", row["body"])
        self.assertEqual(local.local_status(row, self.dir), "same")

    def test_meme_nom_autre_contenu(self):
        row = inspected(cand("s"))
        self._put("s", "# ma version\n")
        self.assertEqual(local.local_status(row, self.dir), "other")

    def test_crlf_de_npx_skills_sous_windows(self):
        row = inspected(cand("s"))
        self._put("s", row["body"].replace("\n", "\r\n"))
        self.assertEqual(local.local_status(row, self.dir), "same")

    def test_compare_a_tous_les_skill_md_du_depot(self):
        # affaan-m/ecc : l'original et ses traductions ; l'installé est l'un d'eux.
        row = inspected(cand("s"))
        installed = "# original\n"
        row["skill_md_paths"] = [".agents/skills/s/SKILL.md", "skills/s/SKILL.md"]
        row["skill_files"] = {".agents/skills/s/SKILL.md": row["skill_md_sha"],
                              "skills/s/SKILL.md": github.git_blob_sha(installed.encode())}
        self._put("s", installed)
        self.assertEqual(local.local_status(row, self.dir), "same")

    def test_trouve_par_le_nom_du_frontmatter(self):
        row = dict(inspected(cand("id-skills-sh")), md_name="vrai-nom")
        self._put("vrai-nom", row["body"])
        self.assertEqual(local.local_status(row, self.dir), "same")

    def test_absent_et_nom_dangereux(self):
        self.assertIsNone(local.local_status(inspected(cand("s")), self.dir))
        (self.dir.parent / "SKILL.md").write_text("x")
        self.addCleanup(lambda: (self.dir.parent / "SKILL.md").unlink(missing_ok=True))
        self.assertIsNone(local.local_status(dict(inspected(cand("s")), skill_id=".."),
                                               self.dir))


class CliCase(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.skills = d / "claude" / "skills"
        self.skills.mkdir(parents=True)
        for p in (patch("skillscout.config.CACHE_PATH", str(d / "cache.db")),
                  patch("skillscout.config.skills_dir", return_value=self.skills)):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, argv, candidates, jev, *, inspect=None, search=None):
        out, err = io.StringIO(), io.StringIO()
        self.queries = []

        def fake_search(q):
            self.queries.append(q)
            return search(q) if search else candidates
        self.inspected = []

        def fake_inspect(c, cache, now):
            self.inspected.append(c["skill_id"])
            return (inspect or inspected)(c)
        with patch("skillscout.sources.search_skills", side_effect=fake_search), \
             patch("skillscout.inspection.inspect_candidate", side_effect=fake_inspect), \
             patch("skillscout.jev.JevClient.from_env", return_value=jev), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()


class TestCliLots(CliCase):
    MANY = [cand(f"s{i:02}", f"org/r{i:02}", rank_=i) for i in range(80)]

    def test_requetes_q_envoyees_et_besoin_juge(self):
        jev = NeedJev(lambda d: 3.0)
        code, out, _ = self.run_cli(["mon besoin", "-q", "eval harness", "-q", "llm judge"],
                                    [cand("a")], jev)
        self.assertEqual(code, 0)
        self.assertEqual(self.queries, ["eval harness", "llm judge"])

    def test_sans_q_le_besoin_est_la_requete(self):
        self.run_cli(["mon besoin"], [cand("a")], NeedJev(lambda d: 3.0))
        self.assertEqual(self.queries, ["mon besoin"])

    def test_s_arrete_au_premier_lot_si_dix_pertinents(self):
        jev = NeedJev(lambda d: 3.0)
        code, out, _ = self.run_cli(["x"], self.MANY, jev)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.inspected), config.SEARCH_BATCH)
        self.assertIn("TOP 5 par pertinence (Jev)", out)

    def test_fouille_les_lots_suivants_jusqu_au_plafond(self):
        # Seuls s30 et s60 sont pertinents : il faut trois lots pour les voir.
        jev = NeedJev(lambda d: 3.0 if d in ("s30", "s60") else 0.5)
        code, out, _ = self.run_cli(["x", "-q", "q"], self.MANY, jev)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.inspected), config.SEARCH_CEILING)
        self.assertEqual(jev.calls, config.SEARCH_CEILING)
        self.assertIn("Seulement 2 skill(s) pertinent(s)", out)
        self.assertIn("s30", out)
        self.assertIn("s60", out)
        self.assertNotIn("s01 ", out)
        self.assertNotIn("Astuce", out)                 # des requêtes -q ont été données

    def test_plafond_limit(self):
        jev = NeedJev(lambda d: 0.0)
        self.run_cli(["x", "--limit", "30"], self.MANY, jev)
        self.assertEqual(len(self.inspected), 30)

    def test_astuce_sans_q_quand_peu_de_pertinents(self):
        jev = NeedJev(lambda d: 3.0 if d == "s00" else 0.0)
        code, out, _ = self.run_cli(["un besoin"], self.MANY, jev)
        self.assertEqual(code, 0)
        self.assertIn('Astuce', out)
        self.assertIn('skillscout "un besoin" -q', out)

    def test_aucun_pertinent_rend_1_avec_astuce(self):
        code, _, err = self.run_cli(["un besoin"], self.MANY[:5], NeedJev(lambda d: 0.0))
        self.assertEqual(code, 1)
        self.assertIn("Aucun skill pertinent parmi les 5", err)
        self.assertIn("Astuce", err)

    def test_sans_jev_ordre_skills_sh_et_un_seul_lot(self):
        code, out, err = self.run_cli(["x", "--no-jev"], self.MANY, None,
                                      inspect=lambda c: inspected(c, score=float(c["relevance_rank"])))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.inspected), config.SEARCH_BATCH)
        self.assertIn("par pertinence skills.sh", out)
        self.assertLess(out.index("s00"), out.index("s04"))   # score plus bas, mais plus pertinent
        self.assertNotIn("s05", out)                              # 5 affichés au plus

    def test_copies_et_deja_installe_affiches(self):
        cands = [cand("evals", "fork/a", rank_=0), cand("evals", "orig/b", rank_=1),
                 cand("autre", "org/c", rank_=2)]
        body = inspected(cands[0])["body"]
        (self.skills / "evals").mkdir()
        (self.skills / "evals" / "SKILL.md").write_bytes(body.encode())
        (self.skills / "autre").mkdir()
        (self.skills / "autre" / "SKILL.md").write_text("# ma version\n")
        jev = NeedJev(lambda d: 3.0)
        code, out, _ = self.run_cli(["x"], cands, jev)
        self.assertEqual(code, 0)
        self.assertEqual(jev.calls, 2)                    # la copie n'est pas jugée
        self.assertIn("+ déjà installé chez vous", out)
        self.assertIn("1 copie(s) identique(s) publiée(s) ailleurs", out)
        self.assertNotIn("retouchée", out)
        self.assertIn("− un autre skill de ce nom est déjà installé chez vous", out)

    def test_json_porte_installed_et_copies(self):
        cands = [cand("evals", "fork/a"), cand("evals", "orig/b", rank_=1)]
        code, out, _ = self.run_cli(["x", "--json"], cands, NeedJev(lambda d: 3.0))
        row = json.loads(out)[0]
        self.assertIsNone(row["installed"])
        self.assertEqual(len(row["copies"]), 1)
        self.assertEqual(row["variants"], [])
        self.assertNotIn("_members", row)

    def test_variantes_affichees_et_non_jugees(self):
        cands = [cand("evals", "fork/a", rank_=0), cand("evals", "orig/b", rank_=1)]
        jev = NeedJev(lambda d: 3.0)
        code, out, _ = self.run_cli(
            ["x"], cands, jev,
            inspect=lambda c: inspected(c, score=90.0 if c["source"] == "orig/b" else 40.0,
                                        content=f"# {c['source']}\n"))
        self.assertEqual(code, 0)
        self.assertEqual(jev.calls, 1)
        self.assertIn("orig/b", out)
        self.assertNotIn("fork/a", out)
        self.assertIn("1 version(s) retouchée(s) publiée(s) ailleurs", out)


class TestCliRevue(CliCase):
    """Défauts relevés par la relecture de la branche (2026-09-29)."""
    EVIL = answers(3.0)
    EVIL["exfiltration"] = {"noul": 0.95}

    def test_version_dangereuse_ne_cache_pas_la_version_saine(self):
        cands = [cand("s", "gros/evil", rank_=0), cand("s", "petit/good", rank_=1)]
        jev = ScriptedJev(lambda d: self.EVIL if d == "evil" else answers(3.0))
        code, out, _ = self.run_cli(
            ["x", "--show-excluded"], cands, jev,
            inspect=lambda c: described(c, c["source"].split("/")[1],
                                        score=95.0 if "gros" in c["source"] else 40.0))
        self.assertEqual(code, 0)
        self.assertEqual(jev.calls, 2)
        self.assertIn("petit/good", out)
        self.assertIn("1 autre(s) version(s) écartée(s)", out)
        self.assertIn("1 écarté(s) sur 2 examiné(s)", out)
        self.assertIn("exfiltration", out.split("Écartés")[1])

    def test_version_hors_sujet_laisse_place_a_la_suivante(self):
        cands = [cand("s", "gros/off", rank_=0), cand("s", "petit/good", rank_=1)]
        jev = ScriptedJev(lambda d: answers(0.0 if d == "off" else 3.0))
        code, out, _ = self.run_cli(
            ["x"], cands, jev,
            inspect=lambda c: described(c, c["source"].split("/")[1],
                                        score=95.0 if "gros" in c["source"] else 40.0))
        self.assertEqual(code, 0)
        self.assertIn("petit/good", out)
        self.assertIn("1 version(s) retouchée(s)", out)   # la version hors sujet reste citée

    def test_forks_ecartes_comptes_et_listes(self):
        cands = [cand("s", f"perso/f{i}", rank_=i) for i in range(5)] + \
                [cand("s", "org/orig", rank_=5)]
        code, out, _ = self.run_cli(
            ["x", "--show-excluded"], cands, NeedJev(lambda d: 3.0),
            inspect=lambda c: inspected(c, excluded=c["source"].startswith("perso/")))
        self.assertEqual(code, 0)
        self.assertIn("5 écarté(s) sur 6 examiné(s)", out)
        self.assertNotIn("copie(s)", out)
        self.assertIn("− 5 autre(s) version(s) écartée(s) (--show-excluded)", out)
        self.assertEqual(out.split("Écartés (5)")[1].count("perso/f"), 5)

    def test_panne_de_jev_au_deuxieme_lot_signalee_et_arretee(self):
        many = [cand(f"s{i:02}", f"org/r{i:02}", rank_=i) for i in range(80)]
        jev = ScriptedJev(lambda d: answers(3.0 if d == "s00" else 0.0),
                          fail_after=config.SEARCH_BATCH)
        code, out, err = self.run_cli(["x", "-q", "q"], many, jev)
        self.assertEqual(code, 0)
        self.assertIn("Jev indisponible à partir du lot 2 (HTTP 429)", err)
        self.assertIn("résultats partiels", err)
        self.assertEqual(len(self.inspected), 2 * config.SEARCH_BATCH)   # pas de lot 3
        self.assertEqual(jev.calls, 2 * config.SEARCH_BATCH)

    def test_skill_injugeable_n_est_pas_une_panne(self):
        # Revue 2 : une version promue trop longue pour Jev arrêtait toute la recherche.
        cands = [cand("evals", "big/evil", rank_=0), cand("evals", "small/huge", rank_=1)] + \
                [cand(f"g{i:02}", f"org/g{i:02}", rank_=2 + i) for i in range(40)]
        evil = answers(3.0)
        evil["exfiltration"] = {"noul": 0.95}

        def inspect(c):
            if c["source"] == "small/huge":
                return described(c, "huge", score=40.0) | {"body": "x" * (v.JEV_TEXT_LIMIT + 1)}
            return described(c, c["source"].split("/")[1],
                             score=95.0 if c["source"] == "big/evil" else 80.0)
        jev = ScriptedJev(lambda d: evil if d == "evil" else answers(0.0 if d < "g23" else 3.0))
        code, out, err = self.run_cli(["x", "-q", "q"], cands, jev, inspect=inspect)
        self.assertEqual(code, 0)
        self.assertNotIn("Jev indisponible", err)
        self.assertGreater(len(self.inspected), config.SEARCH_BATCH)
        self.assertIn("TOP 5 par pertinence (Jev)", out)

    def test_jev_en_panne_des_le_debut_repli_au_premier_lot(self):
        # Revue 3 : un skill trop long dans le lot 1 retardait le repli d'un lot.
        many = [cand(f"s{i:02}", f"org/r{i:02}", rank_=i) for i in range(60)]

        def inspect(c):
            row = inspected(c)
            return row | {"body": "x" * (v.JEV_TEXT_LIMIT + 1)} if c["skill_id"] == "s03" else row
        jev = ScriptedJev(lambda d: answers(3.0), fail_after=0)
        code, out, err = self.run_cli(["x", "-q", "q"], many, jev, inspect=inspect)
        self.assertEqual(code, 0)
        self.assertIn("Jev indisponible (HTTP 429) — classement déterministe seul", err)
        self.assertEqual(len(self.inspected), config.SEARCH_BATCH)
        self.assertEqual(jev.calls, config.SEARCH_BATCH - 1)
        self.assertIn("par pertinence skills.sh", out)
        self.assertNotIn("non jugé", out)

    def test_une_seule_erreur_d_appel_n_arrete_pas(self):
        many = [cand(f"s{i:02}", f"org/r{i:02}", rank_=i) for i in range(60)]
        state = {"n": 0}

        def reply(d):
            state["n"] += 1
            return None if d == "s30" else answers(3.0 if d in ("s00", "s40") else 0.0)
        code, out, err = self.run_cli(["x", "-q", "q"], many, ScriptedJev(reply))
        self.assertEqual(code, 0)
        self.assertNotIn("Jev indisponible", err)
        self.assertIn("s40", out)
        self.assertIn("1 non jugé(s) par Jev", out)

    def test_version_d_un_lot_sans_nom_nouveau_promue(self):
        # Revue 2 : un lot n'apportant que des versions de noms connus ne promouvait rien.
        cands = [cand("evals", "big/off", rank_=0)] + \
                [cand(f"z{i:02}", f"org/z{i:02}", rank_=1 + i) for i in range(24)] + \
                [cand("evals", "small/good", rank_=25)] + \
                [cand(f"z{i:02}", f"autre/z{i:02}", rank_=26 + i) for i in range(24)]
        jev = ScriptedJev(lambda d: answers(3.0 if d == "good" else 0.0))
        code, out, _ = self.run_cli(
            ["x", "-q", "q"], cands, jev,
            inspect=lambda c: described(c, c["source"].split("/")[1],
                                        score=95.0 if c["source"] == "big/off" else 40.0))
        self.assertEqual(code, 0)
        self.assertIn("small/good", out)

    def test_non_juges_comptes_dans_l_en_tete(self):
        cands = [cand("a", rank_=0), cand("b", rank_=1)]
        jev = ScriptedJev(lambda d: answers(3.0) if d == "a" else None)
        code, out, _ = self.run_cli(["x", "-q", "q"], cands, jev)
        self.assertEqual(code, 0)
        self.assertIn("1 non jugé(s) par Jev", out)

    def test_variante_installee_reconnue(self):
        cands = [cand("s", "org/a", rank_=0), cand("s", "org/b", rank_=1)]
        rows = {c["source"]: described(c, c["source"], score=90.0 if c["source"] == "org/a"
                                       else 50.0) for c in cands}
        (self.skills / "s").mkdir()
        (self.skills / "s" / "SKILL.md").write_bytes(rows["org/b"]["body"].encode())
        code, out, _ = self.run_cli(["x"], cands, NeedJev(lambda d: 3.0),
                                    inspect=lambda c: rows[c["source"]])
        self.assertEqual(code, 0)
        self.assertIn("+ variante installée (org/b)", out)

    def test_aucun_candidat_inspecte(self):
        def boom(c):
            raise github.GhError("403")
        code, _, err = self.run_cli(["x"], [cand("a")], NeedJev(lambda d: 3.0), inspect=boom)
        self.assertEqual(code, 1)
        self.assertIn("Aucun candidat n'a pu être inspecté (1 ignoré(s))", err)


class TestTop5(CliCase):
    MANY = [cand(f"s{i:02}", f"org/r{i:02}", rank_=i) for i in range(30)]
    GOOD = {"examples": {"noul": 0.9}, "steps": {"noul": 0.8}, "third_party": {"noul": 0.1},
            "writing": {"score": 2.7}}

    def test_cinq_au_plus_blocs_separes_par_une_ligne_vide(self):
        jev = NeedJev(lambda d: 3.0, explain=lambda d: self.GOOD)
        code, out, _ = self.run_cli(["x", "-q", "q"], self.MANY, jev)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.inspected), config.SEARCH_BATCH)   # fouille jusqu'à 10 pertinents
        self.assertIn(" 5. s04", out)
        self.assertNotIn(" 6. ", out)
        self.assertEqual(sorted(jev.explain_calls), ["s00", "s01", "s02", "s03", "s04"])
        self.assertIn("besoin 3.0/3 · méta 1.0/3 · substance 3.0/3 · écriture 2.7/3", out)
        self.assertIn("    + prompt bien écrit pour un LLM : précis et détaillé (2.7/3)", out)
        self.assertIn("    + donne des exemples concrets", out)
        self.assertIn("    + étapes claires, dans l'ordre", out)
        self.assertIn("    + utilisable sans service tiers", out)
        blocks = out.split("\n\n")
        self.assertEqual(sum(1 for b in blocks if b.lstrip().startswith(("1.", "2.", "3.", "4.", "5."))), 5)

    def test_faiblesses_d_ecriture_et_de_dependance(self):
        bad = {"examples": {"noul": 0.1}, "steps": {"noul": 0.2}, "third_party": {"noul": 0.9},
               "writing": {"score": 1.2}}
        jev = NeedJev(lambda d: 3.0, explain=lambda d: bad)
        code, out, _ = self.run_cli(["x", "-q", "q"], self.MANY[:1], jev)
        self.assertEqual(code, 0)
        self.assertIn("− prompt mal écrit pour un LLM : vague ou lacunaire (1.2/3)", out)
        self.assertIn("− peu d'exemples concrets", out)
        self.assertIn("− pas de marche à suivre claire", out)
        self.assertIn("− ne sert qu'avec une plateforme, un service ou un compte précis", out)

    def test_explication_incomplete_ne_dit_rien(self):
        partial = {"examples": {"noul": 0.9}}            # « writing » manque
        jev = NeedJev(lambda d: 3.0, explain=lambda d: partial)
        code, out, _ = self.run_cli(["x", "-q", "q", "--json"], self.MANY[:1], jev)
        row = json.loads(out)[0]
        self.assertIsNone(row["explain"])
        self.assertNotIn("écriture", row["relevance"])
        self.assertIn("strengths", row)
        self.assertIn("weaknesses", row)

    def test_sans_jev_pas_d_explication(self):
        jev = NeedJev(lambda d: 3.0, explain=lambda d: self.GOOD)
        code, out, _ = self.run_cli(["x", "--no-jev"], self.MANY, jev)
        self.assertEqual(code, 0)
        self.assertEqual(jev.explain_calls, [])
        self.assertNotIn("écriture", out)
        self.assertIn("− éditeur non vérifié (confiance 80/100)", out)   # mesures seules


class TestExplainRules(unittest.TestCase):
    def _row(self, **kw):
        j = v.Judgement("ok", {k: 0.0 for k in v.DANGERS}, kw.pop("severity", 0.0),
                        {"need": kw.pop("need", 3.0), "meta": kw.pop("meta", 1.0),
                         "substance": kw.pop("substance", 2.0)})
        return dict({"jev": j, "flags": [], "score": 50.0, "installs": 500}, **kw)

    def test_pertinence_et_substance(self):
        plus, minus = explain.strengths_weaknesses(self._row(need=2.2, substance=1.0))
        self.assertIn("répond clairement au besoin (2.2/3)", plus)
        self.assertIn("contenu mince (1.0/3)", minus)
        plus, minus = explain.strengths_weaknesses(self._row(need=1.7))
        self.assertIn("ne répond qu'en partie au besoin (1.7/3)", minus)

    def test_indicateurs_du_tri_traduits(self):
        flags = ["éditeur en liste blanche", "⚠ 3 fichiers exécutables",
                 "⚠ SKILL.md : téléchargement exécuté (curl/wget | sh)",
                 "⚠ Jev : téléchargement exécuté 0.70", "⚠ non maintenu depuis plus d'un an"]
        plus, minus = explain.strengths_weaknesses(self._row(flags=flags, installs=25_000))
        self.assertIn("éditeur reconnu (liste blanche)", plus)
        self.assertIn("très utilisé (25 000 installations)", plus)
        self.assertIn("contient 3 fichier(s) exécutable(s)", minus)
        self.assertIn("le texte demande : téléchargement exécuté (curl/wget | sh)", minus)
        self.assertIn("Jev y soupçonne : téléchargement exécuté (0.70)", minus)
        self.assertIn("non maintenu depuis plus d'un an", minus)
        self.assertFalse(any("éditeur non vérifié" in m for m in minus))

    def test_editeur_non_verifie_et_peu_utilise(self):
        plus, minus = explain.strengths_weaknesses(self._row(score=42.0, installs=12))
        self.assertIn("éditeur non vérifié (confiance 42/100)", minus)
        self.assertIn("peu utilisé (12 installation(s))", minus)

    def test_seuils_sur_la_note_affichee(self):
        # 2.49 s'affiche 2.5 : même phrase que 2.50.
        for need in (2.49, 2.50):
            plus, _ = explain.strengths_weaknesses(self._row(need=need))
            self.assertIn("répond exactement au besoin (2.5/3)", plus, need)

    def test_gravite(self):
        _, minus = explain.strengths_weaknesses(self._row(severity=2.0))
        self.assertIn("dégâts possibles s'il est suivi à la lettre (gravité 2.0/3)", minus)


class TestExplainOne(unittest.TestCase):
    class Client:
        def __init__(self, reply):
            self.reply, self.questions = reply, None

        def classify(self, state, questions):
            self.questions = questions
            return self.reply

    def test_noul_et_score(self):
        c = self.Client({"examples": {"noul": 0.9}, "steps": {"noul": 0.1},
                         "third_party": {"noul": 0.5}, "writing": {"score": 2.5}})
        out = v.explain_one(inspected(cand("s")), c)
        self.assertEqual(out, {"examples": 0.9, "steps": 0.1, "third_party": 0.5, "writing": 2.5})
        self.assertEqual(c.questions["writing"]["type"], "score")
        self.assertIn("DATA", c.questions["writing"]["instructions"])

    def test_reponse_absente_ou_hors_bornes(self):
        for reply in (None, {"examples": {"noul": 0.9}},
                      {"examples": {"noul": 0.9}, "steps": {"noul": 0.1},
                       "third_party": {"noul": 0.5}, "writing": {"score": 4.0}}):
            self.assertIsNone(v.explain_one(inspected(cand("s")), self.Client(reply)), reply)

    def test_texte_trop_long_ou_absent_pas_envoye(self):
        c = self.Client({})
        self.assertIsNone(v.explain_one(dict(inspected(cand("s")), body=None), c))
        self.assertIsNone(v.explain_one(dict(inspected(cand("s")),
                                             body="x" * (v.JEV_TEXT_LIMIT + 1)), c))
        self.assertIsNone(c.questions)


class TestCasDeReference(CliCase):
    """Besoin réel du 2026-09-29, réponses skills.sh enregistrées le même jour.
    Le top 3 de référence (proposé par Claude) doit sortir dans le top 10 :
    eval-harness (affaan-m/ecc), eval-harness-first (wshobson/agents) et le
    pack evals-skills. Avant la correction, le tri par installations les
    coupait avant même l'inspection."""
    NEED = "Define measurable success criteria for your LLM application and build evaluations to test it"
    FILES = {"eval harness": "eval_harness", "llm evals": "llm_evals",
             "error analysis": "error_analysis", NEED: "phrase"}
    EVAL_WORDS = ("eval", "judge", "error-analysis")

    def _search(self, q):
        data = json.loads((FIXTURES / f"{self.FILES[q]}.json").read_text(encoding="utf-8"))
        return [c for c in (sources._candidate(s, i) for i, s in enumerate(data["skills"])) if c]

    def _jev(self):
        # Jev factice « parfait » : pertinent si le skill parle d'évaluation.
        return NeedJev(lambda d: 3.0 if any(w in d.lower() for w in self.EVAL_WORDS) else 0.0)

    def _top(self, argv):
        code, out, _ = self.run_cli(argv + ["--json"], None, self._jev(), search=self._search)
        self.assertEqual(code, 0)
        # Chaque ligne compte avec ses copies et variantes regroupées.
        return {f"{src}/{r['skill_id']}" for r in json.loads(out)
                for src in [r["source"], *r["copies"], *r["variants"]]}

    def test_avec_les_requetes_du_domaine(self):
        top = self._top([self.NEED, "-q", "eval harness", "-q", "llm evals", "-q", "error analysis"])
        self.assertIn("affaan-m/ecc/eval-harness", top)
        self.assertIn("wshobson/agents/eval-harness-first", top)
        pack = ("hamelsmu/evals-skills/", "ai-evals-course/evals-skills/")
        self.assertTrue(any(k.startswith(pack) for k in top), top)

    def test_la_phrase_seule_ne_suffit_pas(self):
        # Constat qui justifie -q : sur la phrase entière, skills.sh ne renvoie
        # aucun des trois. Si ce test casse, skills.sh a progressé.
        code, _, err = self.run_cli([self.NEED], None, self._jev(), search=self._search)
        self.assertEqual(code, 1)
        self.assertIn("Astuce", err)


if __name__ == "__main__":
    unittest.main()
