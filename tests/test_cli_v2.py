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
        # Revue finale M2 : une simulation n'annonce pas d'installation.
        self.assertIn("Simulation : 1 à installer, 1 en attente, 3 écarté(s)", out)
        self.assertNotIn("installé(s)", out)

    def test_routine_reelle_annonce_les_installations(self):
        res = routine.RunResult(run_id=NOW, installed=[{"skill_id": "a"}],
                                pending=[{}], rejected=[("x", "y")] * 3)
        with patch("skillscout.jev.JevClient.from_env", return_value="client"), \
             patch("skillscout.routine.run_routine", return_value=res):
            code, out, _ = self.main(["routine"])
        self.assertEqual(code, 0)
        self.assertIn("1 installé(s), 1 en attente, 3 écarté(s)", out)
        self.assertNotIn("Simulation", out)

    def test_routine_code_de_sortie_si_echec(self):
        res = routine.RunResult(run_id=NOW, status="jev_unavailable")
        with patch("skillscout.jev.JevClient.from_env", return_value=None), \
             patch("skillscout.routine.run_routine", return_value=res):
            code, out, _ = self.main(["routine"])
        self.assertEqual(code, 1)
        self.assertIn("Jev indisponible", out)
        res = routine.RunResult(run_id=NOW, status="source_error")
        with patch("skillscout.jev.JevClient.from_env", return_value=None), \
             patch("skillscout.routine.run_routine", return_value=res):
            code, out, _ = self.main(["routine"])
        self.assertEqual(code, 1)
        self.assertIn("injoignable", out)

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


def preflight(version=None, file=None, code=0, stderr=""):
    """Sortie simulée de la vérification préalable (jamais lancée pour de vrai)."""
    import skillscout
    out = "" if code else f"{version or skillscout.__version__}\n{file or skillscout.__file__}\n"
    return subprocess.CompletedProcess([], code, stdout=out, stderr=stderr)


class TestSchedule(unittest.TestCase):
    def test_script_register(self):
        s = schedule.register_script(r"C:\it's\pythonw.exe", Path(r"C:\l'app\site-packages"))
        self.assertIn(r"-Execute 'C:\it''s\pythonw.exe'", s)
        self.assertIn("-Argument '-m skillscout routine'", s)
        self.assertIn(r"-WorkingDirectory 'C:\l''app\site-packages'", s)
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
        with patch("skillscout.schedule._preflight", return_value=preflight()), \
             patch("skillscout.schedule._powershell", return_value=ok) as ps, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(schedule.register(is_windows=True), 0)
        self.assertIn(schedule.TASK_NAME, ps.call_args.args[0])
        err = io.StringIO()
        with patch("skillscout.schedule._preflight", return_value=preflight()), \
             patch("skillscout.schedule._powershell", return_value=ko), \
             contextlib.redirect_stderr(err):
            self.assertEqual(schedule.register(is_windows=True), 1)
        self.assertIn("Accès refusé", err.getvalue())

    # -- Revue finale C1 : la tâche doit lancer CE skillscout, pas une ancienne version --

    def test_dossier_de_travail_du_paquet(self):
        import skillscout
        workdir = schedule.package_dir()
        self.assertEqual(workdir, Path(skillscout.__file__).resolve().parent.parent)
        self.assertTrue((workdir / "skillscout" / "__init__.py").exists())

    def test_register_demarre_la_tache_dans_le_dossier_du_paquet(self):
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("skillscout.schedule._preflight", return_value=preflight()) as pf, \
             patch("skillscout.schedule._powershell", return_value=ok) as ps, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(schedule.register(is_windows=True), 0)
        workdir = schedule.package_dir()
        self.assertEqual(pf.call_args.args[0], workdir)
        self.assertIn(f"-WorkingDirectory {schedule._ps_quote(str(workdir))}",
                      ps.call_args.args[0])
        import skillscout
        self.assertIn(skillscout.__version__, out.getvalue())

    def test_register_refuse_une_autre_version_ou_un_autre_fichier(self):
        import skillscout
        cas = {"version": preflight(version="0.2.0",
                                    file=r"C:\Python314\Lib\site-packages\skillscout.py"),
               "fichier": preflight(file=str(self.root_file())),
               "import": preflight(code=1, stderr="ModuleNotFoundError: No module named "
                                                  "'skillscout'")}
        for nom, sortie in cas.items():
            err = io.StringIO()
            with patch("skillscout.schedule._preflight", return_value=sortie), \
                 patch("skillscout.schedule._powershell") as ps, \
                 contextlib.redirect_stderr(err):
                self.assertEqual(schedule.register(is_windows=True), 1, nom)
            ps.assert_not_called()
            self.assertIn("py -3.14 -m pip install --upgrade .", err.getvalue(), nom)
        self.assertIn("0.2.0", self._refus(cas["version"]))
        self.assertIn("ModuleNotFoundError", self._refus(cas["import"]))
        self.assertIn(skillscout.__version__, self._refus(cas["fichier"]))

    def test_register_refuse_si_la_verification_echoue(self):
        for exc in (OSError("introuvable"), subprocess.TimeoutExpired("py", 60)):
            with patch("skillscout.schedule._preflight", side_effect=exc), \
                 patch("skillscout.schedule._powershell") as ps, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(schedule.register(is_windows=True), 1)
            ps.assert_not_called()

    def test_preflight_lance_l_interpreteur_courant_depuis_le_dossier(self):
        done = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("skillscout.schedule.subprocess.run", return_value=done) as run:
            schedule._preflight(Path(r"C:\dossier"))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], [sys.executable, "-c"])
        self.assertIn("skillscout.__version__", cmd[2])
        self.assertIn("skillscout.__file__", cmd[2])
        kw = run.call_args.kwargs
        self.assertEqual(kw["cwd"], Path(r"C:\dossier"))
        self.assertEqual(kw["timeout"], 60)
        self.assertTrue(kw["capture_output"])
        self.assertEqual(kw["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @staticmethod
    def root_file():
        return Path(tempfile.gettempdir()) / "ailleurs" / "skillscout" / "__init__.py"

    @staticmethod
    def _refus(sortie):
        err = io.StringIO()
        with patch("skillscout.schedule._preflight", return_value=sortie), \
             patch("skillscout.schedule._powershell"), contextlib.redirect_stderr(err):
            schedule.register(is_windows=True)
        return err.getvalue()

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
