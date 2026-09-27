import os, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import config, profile


class TestPaths(unittest.TestCase):
    def test_under(self):
        root = Path(tempfile.mkdtemp())
        p = config.Paths.under(root)
        self.assertEqual(p.skills_dir, root / "claude" / "skills")
        self.assertEqual(p.staging_dir, root / "claude" / ".skillscout-staging")
        self.assertEqual(p.manifest, root / "config" / "installed.json")
        self.assertEqual(p.profile, root / "config" / "profile.toml")
        self.assertEqual(p.lock_file, root / "cache" / "routine.lock")
        self.assertEqual(p.journal, root / "cache" / "journal.jsonl")
        self.assertEqual(p.reports_dir, root / "cache" / "reports")
        self.assertEqual(p.cache_db, root / "cache" / "cache.db")

    def test_defaut_suit_cache_path(self):
        with patch("skillscout.config.CACHE_PATH", os.path.join("X", "cache.db")):
            p = config.default_paths()
        self.assertEqual(p.cache_dir, Path("X"))
        self.assertEqual(p.skills_dir, Path.home() / ".claude" / "skills")
        self.assertEqual(p.skill_lock, Path.home() / ".agents" / ".skill-lock.json")


class TestProfile(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = config.Paths.under(self.root)

    def test_profil_par_defaut(self):
        p = profile.parse_profile(profile.default_profile_text())
        self.assertEqual(p.max_installs, 3)
        self.assertEqual(p.min_installs, 100)
        self.assertEqual((p.per_query, p.leaderboard_top, p.max_candidates), (25, 50, 300))
        self.assertEqual(len(p.meta_themes), 12)
        self.assertIn("rust", p.stack_themes)
        self.assertIn("Supabase", p.stack_description)

    def test_cree_le_fichier_au_premier_lancement(self):
        self.assertFalse(self.paths.profile.exists())
        p = profile.load_profile(self.paths)
        self.assertEqual(self.paths.profile.read_text(encoding="utf-8"),
                         profile.default_profile_text())
        self.assertEqual(p.max_installs, 3)

    def test_lit_la_copie_modifiee(self):
        self.paths.config_dir.mkdir(parents=True)
        text = profile.default_profile_text().replace("max_installs = 3", "max_installs = 1")
        self.paths.profile.write_text(text, encoding="utf-8")
        self.assertEqual(profile.load_profile(self.paths).max_installs, 1)

    def test_invalide(self):
        base = profile.default_profile_text()
        for bad in ("[meta\n", base.replace("max_installs = 3", "max_installs = -1"),
                    base.replace("max_installs = 3", "max_installs = 99"),
                    base.replace('themes = ["python"', 'themes = [1, "python"'),
                    base.replace("per_query = 25", 'per_query = "25"'),
                    "[meta]\nthemes = []\n"):
            with self.assertRaises(profile.ProfileError, msg=bad[:30]):
                profile.parse_profile(bad)

    def test_texte_pour_jev(self):
        t = profile.parse_profile(profile.default_profile_text()).jev_text()
        self.assertIn("tdd", t)
        self.assertIn("bevy", t)
        self.assertIn("PyQt6", t)


if __name__ == "__main__":
    unittest.main()
