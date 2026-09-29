import sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import sources


class TestSearchRobuste(unittest.TestCase):
    """Revue finale I2 (b) : un résultat étrange de skills.sh n'arrête rien."""

    def test_installs_illisible_et_source_non_texte(self):
        data = {"skills": [{"skillId": "a", "source": "o/r", "installs": "1.2k"},
                           {"skillId": "b", "source": 42, "installs": 9},
                           {"skillId": 7, "source": "o/r"}, "pas un objet",
                           {"skillId": "c", "source": "o/c", "installs": float("inf")}]}
        with patch("skillscout.net.get_json", return_value=data):
            out = sources.search_skills("x")
        self.assertEqual([(s["skill_id"], s["installs"]) for s in out], [("a", 0), ("c", 0)])

    def test_reponse_inattendue_leve_searcherror(self):
        for data in ([], "x", {"skills": "x"}):
            with patch("skillscout.net.get_json", return_value=data):
                with self.assertRaises(sources.SearchError, msg=repr(data)):
                    sources.search_skills("x")


if __name__ == "__main__":
    unittest.main()
