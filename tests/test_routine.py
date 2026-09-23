import json, os, shutil, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import config, github, install, profile, report, routine, sources, verdict as v

PROFILE = """[meta]
themes = ["workflow"]
[stack]
description = "Python et Rust"
themes = ["python"]
[routine]
max_installs = 2
min_installs = 100
per_query = 25
leaderboard_top = 0
max_candidates = 300
"""
NOW = 1790589600.0            # 2026-09-28T10:00:00Z (lundi, semaine ISO 40)


def cand(skill_id, source="obra/superpowers", installs=500):
    return {"skill_id": skill_id, "name": skill_id, "source": source, "installs": installs,
            "relevance_rank": 0}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = config.Paths.under(self.root)
        self.paths.skills_dir.mkdir(parents=True)
        self.paths.config_dir.mkdir(parents=True)
        self.paths.profile.write_text(PROFILE, encoding="utf-8")

    def prof(self, **changes):
        text = PROFILE
        for k, val in changes.items():
            text = "\n".join(f"{k} = {val}" if line.startswith(f"{k} =") else line
                             for line in text.splitlines())
        return profile.parse_profile(text)


class TestRunId(unittest.TestCase):
    def test_format_et_semaine(self):
        self.assertEqual(routine.run_id_of(NOW), "2026-09-28T10:00:00Z")
        self.assertEqual(report.iso_week_name("2026-09-28T10:00:00Z"), "2026-W40")
        self.assertEqual(report.iso_week_name("2027-01-01T00:00:00Z"), "2026-W53")


class TestVerrou(Base):
    def test_un_seul_detenteur(self):
        lock = self.paths.lock_file
        self.assertTrue(routine.acquire_lock(lock))
        self.assertFalse(routine.acquire_lock(lock))
        routine.release_lock(lock)
        self.assertTrue(routine.acquire_lock(lock))
        routine.release_lock(lock)
        self.assertFalse(lock.exists())

    def test_verrou_orphelin_repris(self):
        lock = self.paths.lock_file
        self.assertTrue(routine.acquire_lock(lock))
        old = time.time() - routine.LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        self.assertTrue(routine.acquire_lock(lock))
        routine.release_lock(lock)

    def test_os_open_permissionerror(self):
        lock = self.paths.lock_file
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
        with patch("skillscout.routine.os.open", side_effect=PermissionError):
            self.assertFalse(routine.acquire_lock(lock))

    def test_unlink_permissionerror(self):
        lock = self.paths.lock_file
        self.assertTrue(routine.acquire_lock(lock))
        old = time.time() - routine.LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        with patch.object(type(lock), "unlink", side_effect=PermissionError):
            self.assertFalse(routine.acquire_lock(lock))

    def test_verrou_concurrent_threads(self):
        lock = self.paths.lock_file
        self.assertTrue(routine.acquire_lock(lock))
        old = time.time() - routine.LOCK_STALE_S - 60
        os.utime(lock, (old, old))
        results = []
        barrier = threading.Barrier(8)
        def race_acquire():
            barrier.wait()
            try:
                results.append(routine.acquire_lock(lock))
            except Exception as e:
                results.append(f"exception: {type(e).__name__}")
        threads = [threading.Thread(target=race_acquire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertNotIn("exception", str(results))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 7)
        routine.release_lock(lock)


class TestDiscover(Base):
    def test_dedoublonne_trie_et_note_les_pannes(self):
        search = {"workflow": [cand("a", installs=10), cand("b", installs=900)],
                  "python": [cand("A", installs=50)]}          # même skill que "a"

        def fake_search(q, limit=25):
            if q not in search:
                raise sources.SearchError("hors ligne")
            self.assertEqual(limit, 25)
            return search[q]
        prof = self.prof(leaderboard_top="5")
        with patch("skillscout.sources.search_skills", side_effect=fake_search), \
             patch("skillscout.sources.fetch_leaderboard",
                   side_effect=[[cand("c", installs=300)],
                                sources.LeaderboardError("format changé")]) as lb:
            cands, errors = routine.discover(prof)
        self.assertEqual([(c["skill_id"], c["installs"]) for c in cands],
                         [("b", 900), ("c", 300), ("A", 50)])
        self.assertEqual(lb.call_args_list[0].kwargs, {"top": 5})
        self.assertEqual(len(errors), 1)
        self.assertIn("hot", errors[0])

    def test_sans_classement_si_leaderboard_top_zero(self):
        with patch("skillscout.sources.search_skills", return_value=[]), \
             patch("skillscout.sources.fetch_leaderboard") as lb:
            routine.discover(self.prof())
        lb.assert_not_called()


class TestReport(Base):
    def _result(self, **kw):
        r = routine.RunResult(run_id="2026-09-28T10:00:00Z", candidates=12, jev_calls=4, **kw)
        return r

    def test_rapport_complet(self):
        r = self._result(
            installed=[{"skill_id": "tdd", "source": "obra/superpowers", "tree_sha": "a" * 40,
                        "installs": 900, "relevance": "méta 3.0/3 · pile 1.0/3",
                        "description": "Use when testing", "path": "C:/x/tdd"}],
            pending=[{"skill_id": "plan", "source": "o/r", "tree_sha": "b" * 40, "installs": 300,
                      "relevance": "méta 2.0/3 · pile 0.0/3", "description": "", "path": ""}],
            rejected=[("evil (x/y)", "Jev : exfiltration 0.91")],
            source_errors=["classement hot : format changé"],
            upstream_changed=["tdd (obra/superpowers)"], errors=["boom"])
        text = report.render(r)
        for needle in ("2026-09-28T10:00:00Z", "## Installés (1)", "**tdd**", "méta 3.0/3",
                       "obra/superpowers@aaaaaaa", "## En attente (1)", "## Écartés (1)",
                       "exfiltration 0.91", "classement hot", "amont", "boom",
                       "skillscout uninstall --last", "Candidats examinés : 12", "appels Jev : 4"):
            self.assertIn(needle, text)

    def test_simulation_et_etat(self):
        text = report.render(self._result(dry_run=True, status="jev_unavailable",
                                          jev_status="TYPESAFE_API_KEY absente"))
        self.assertIn("simulation", text)
        self.assertIn(report.STATUS_LABELS["jev_unavailable"], text)
        self.assertIn("TYPESAFE_API_KEY absente", text)

    def test_ecriture_rapport_et_journal(self):
        r = self._result(installed=[{"skill_id": "tdd", "source": "o/r", "tree_sha": "",
                                     "installs": 1, "relevance": "", "description": "",
                                     "path": ""}])
        p = report.write_report(self.paths, r)
        self.assertEqual(p, self.paths.reports_dir / "2026-W40.md")
        self.assertIn("tdd", p.read_text(encoding="utf-8"))
        report.append_journal(self.paths, r)
        report.append_journal(self.paths, r)
        lines = self.paths.journal.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        entry = json.loads(lines[0])
        self.assertEqual(entry["installed"], ["tdd"])
        self.assertEqual(entry["started_at"], "2026-09-28T10:00:00Z")
        self.assertEqual(entry["candidates"], 12)
        self.assertIn("finished_at", entry)
        import datetime
        dt = datetime.datetime.strptime(entry["finished_at"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertIsNotNone(dt)


if __name__ == "__main__":
    unittest.main()
