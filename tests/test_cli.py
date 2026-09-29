import contextlib, io, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import cli, github


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_search_explicite(self):
        with patch("skillscout.sources.search_skills", return_value=[]), \
             patch("skillscout.config.CACHE_PATH", str(self.root / "c.db")):
            code, _, err = run(["search", "x"])
        self.assertEqual(code, 1)
        self.assertIn("Aucun candidat", err)

    def test_commandes_de_la_routine_retirees(self):
        # 2.2.0 : la routine d'installation automatique est retirée ; ses
        # commandes ne doivent pas être prises pour un besoin à chercher.
        for name in ("routine", "uninstall", "installed"):
            with patch("skillscout.sources.search_skills") as search:
                code, out, err = run([name, "--dry-run"])
            self.assertEqual(code, 2, name)
            self.assertIn("retiré en 2.2.0", err)
            search.assert_not_called()


class TestGhSansFenetre(unittest.TestCase):
    def test_creationflags(self):
        done = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with patch("skillscout.github.subprocess.run", return_value=done) as run_:
            github.gh_json("repos/a/b")
        self.assertEqual(run_.call_args.kwargs["creationflags"], github._NO_WINDOW)


if __name__ == "__main__":
    unittest.main()
