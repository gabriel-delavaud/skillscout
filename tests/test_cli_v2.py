import contextlib, io, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import cli, config, github, install, routine, schedule

NOW = "2026-09-28T10:00:00Z"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = config.Paths.under(self.root)
        self.paths.skills_dir.mkdir(parents=True)
        p = patch("skillscout.config.default_paths", return_value=self.paths)
        p.start()
        self.addCleanup(p.stop)

    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def installer(self, name, run_id=NOW):
        data = {"SKILL.md": f"# {name}\n".encode()}
        expected = {"SKILL.md": github.git_blob_sha(data["SKILL.md"])}
        install.install_skill({"skill_id": name, "source": "o/r", "tree_sha": "a" * 40},
                              data, expected, self.paths, run_id=run_id, scores={}, now=run_id)


class TestDispatch(Base):
    def test_routine_lance_la_routine(self):
        res = routine.RunResult(run_id=NOW, installed=[{"skill_id": "a"}],
                                pending=[{}], rejected=[("x", "y")] * 3)
        with patch("skillscout.jev.JevClient.from_env", return_value="client") as fe, \
             patch("skillscout.routine.run_routine", return_value=res) as rr:
            code, out, _ = self.main(["routine", "--dry-run"])
        self.assertEqual(code, 0)
        fe.assert_called_once()
        self.assertEqual(rr.call_args.args, (self.paths,))
        self.assertEqual(rr.call_args.kwargs, {"client": "client", "dry_run": True})
        self.assertIn("1 installé(s), 1 en attente, 3 écarté(s)", out)

    def test_routine_code_de_sortie_si_echec(self):
        res = routine.RunResult(run_id=NOW, status="jev_unavailable")
        with patch("skillscout.jev.JevClient.from_env", return_value=None), \
             patch("skillscout.routine.run_routine", return_value=res):
            code, out, _ = self.main(["routine"])
        self.assertEqual(code, 1)
        self.assertIn("Jev indisponible", out)

    def test_register_et_unregister(self):
        with patch("skillscout.schedule.register", return_value=0) as reg, \
             patch("skillscout.schedule.unregister", return_value=0) as unreg, \
             patch("skillscout.routine.run_routine") as rr:
            self.assertEqual(self.main(["routine", "--register"])[0], 0)
            self.assertEqual(self.main(["routine", "--unregister"])[0], 0)
        reg.assert_called_once()
        unreg.assert_called_once()
        rr.assert_not_called()

    def test_register_et_unregister_exclusifs(self):
        with self.assertRaises(SystemExit) as ctx:
            self.main(["routine", "--register", "--unregister"])
        self.assertEqual(ctx.exception.code, 2)

    def test_search_explicite(self):
        with patch("skillscout.sources.search_skills", return_value=[]), \
             patch("skillscout.config.CACHE_PATH", str(self.root / "c.db")):
            code, _, err = self.main(["search", "x"])
        self.assertEqual(code, 1)
        self.assertIn("Aucun candidat", err)


class TestUninstallCli(Base):
    def test_uninstall_nom(self):
        self.installer("tdd")
        code, out, _ = self.main(["uninstall", "tdd"])
        self.assertEqual(code, 0)
        self.assertIn("retiré", out)
        self.assertFalse((self.paths.skills_dir / "tdd").exists())

    def test_uninstall_hors_manifeste(self):
        (self.paths.skills_dir / "perso").mkdir()
        code, _, err = self.main(["uninstall", "perso"])
        self.assertEqual(code, 1)
        self.assertIn("n'a pas été installé par skillscout", err)
        self.assertTrue((self.paths.skills_dir / "perso").exists())

    def test_uninstall_last(self):
        self.installer("vieux", run_id="2026-09-21T10:00:00Z")
        self.installer("neuf")
        code, out, _ = self.main(["uninstall", "--last"])
        self.assertEqual(code, 0)
        self.assertIn("neuf", out)
        self.assertTrue((self.paths.skills_dir / "vieux").exists())
        self.assertEqual(self.main(["uninstall", "--last"])[0], 0)
        code, out, _ = self.main(["uninstall", "--last"])
        self.assertIn("Rien à retirer", out)

    def test_uninstall_sans_argument(self):
        with self.assertRaises(SystemExit) as ctx:
            self.main(["uninstall"])
        self.assertEqual(ctx.exception.code, 2)

    def test_installed(self):
        self.assertIn("Aucun skill installé", self.main(["installed"])[1])
        self.installer("tdd")
        code, out, _ = self.main(["installed"])
        self.assertEqual(code, 0)
        self.assertIn("tdd", out)
        self.assertIn("o/r@aaaaaaa", out)


class TestSchedule(unittest.TestCase):
    def test_script_register(self):
        s = schedule.register_script(r"C:\it's\pythonw.exe")
        self.assertIn(r"-Execute 'C:\it''s\pythonw.exe'", s)
        self.assertIn("-Argument '-m skillscout routine'", s)
        self.assertIn("-Weekly -DaysOfWeek Monday -At 10:00", s)
        self.assertIn("-StartWhenAvailable", s)
        self.assertIn("-MultipleInstances IgnoreNew", s)
        self.assertIn(f"-TaskName '{schedule.TASK_NAME}'", s)
        self.assertIn("-Force", s)

    def test_register_hors_windows(self):
        with patch("skillscout.schedule._powershell") as ps, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(schedule.register(is_windows=False), 1)
        ps.assert_not_called()

    def test_register_succes_et_echec(self):
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        ko = subprocess.CompletedProcess([], 1, stdout="", stderr="Accès refusé")
        with patch("skillscout.schedule._powershell", return_value=ok) as ps, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(schedule.register(is_windows=True), 0)
        self.assertIn(schedule.TASK_NAME, ps.call_args.args[0])
        err = io.StringIO()
        with patch("skillscout.schedule._powershell", return_value=ko), \
             contextlib.redirect_stderr(err):
            self.assertEqual(schedule.register(is_windows=True), 1)
        self.assertIn("Accès refusé", err.getvalue())

    def test_unregister_script(self):
        self.assertEqual(schedule.unregister_script(),
                         f"Unregister-ScheduledTask -TaskName '{schedule.TASK_NAME}' -Confirm:$false")


class TestGhSansFenetre(unittest.TestCase):
    def test_creationflags(self):
        done = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with patch("skillscout.github.subprocess.run", return_value=done) as run:
            github.gh_json("repos/a/b")
        self.assertEqual(run.call_args.kwargs["creationflags"], github._NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
