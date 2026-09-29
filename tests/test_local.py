import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import local


class TestNomsSurs(unittest.TestCase):
    """`is_safe_name` est le seul filtre entre un nom venu de skills.sh ou de
    GitHub et un chemin sous ~/.claude/skills (repris de l'ancien test_install)."""

    def test_noms_surs(self):
        for ok in ("tdd", "code-review.v2", "a", "x_1"):
            self.assertTrue(local.is_safe_name(ok), ok)
        for bad in ("", "../x", "a/b", "A", "con", "nul.md", "x.", "-x", "a" * 65, "a\\b", ".."):
            self.assertFalse(local.is_safe_name(bad), bad)

    def test_retour_a_la_ligne_final_refuse(self):
        # `re.match` avec `$` accepte un "\n" final : toujours `fullmatch`.
        self.assertFalse(local.is_safe_name("tdd\n"))


if __name__ == "__main__":
    unittest.main()
