import sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import sources

FIX = Path(__file__).resolve().parent / "fixtures"
HOT = (FIX / "leaderboard_hot.html").read_text(encoding="utf-8")
CASSE = (FIX / "leaderboard_casse.html").read_text(encoding="utf-8")


class TestParseInitialSkills(unittest.TestCase):
    def test_extrait_la_liste(self):
        out = sources.parse_initial_skills(HOT)
        self.assertEqual([s["skill_id"] for s in out],
                         ["brainstorming", "ui-taste", "google-agents-cli-eval", "x"])
        self.assertEqual(out[0], {"skill_id": "brainstorming", "name": "brainstorming",
                                  "source": "obra/superpowers", "installs": 755,
                                  "relevance_rank": 0})

    def test_entrees_etranges_ignorees_ou_neutralisees(self):
        out = {s["skill_id"]: s for s in sources.parse_initial_skills(HOT)}
        self.assertNotIn("sans-source", out)
        self.assertEqual(out["x"]["installs"], 0)      # installs non numérique
        self.assertEqual(out["x"]["relevance_rank"], 5)

    def test_format_casse(self):
        for html in (CASSE, "<html>rien</html>", "",
                     'self.__next_f.push([1,"\\"initialSkills\\":[{\\"source\\""])'):
            with self.assertRaises(sources.LeaderboardError, msg=html[:40]):
                sources.parse_initial_skills(html)


class TestFetchLeaderboard(unittest.TestCase):
    def test_telecharge_et_tronque(self):
        with patch("skillscout.net.get_text", return_value=HOT) as g:
            out = sources.fetch_leaderboard("hot", top=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(g.call_args.args[0], "https://www.skills.sh/hot")

    def test_injoignable(self):
        with patch("skillscout.net.get_text", side_effect=OSError("dns")):
            with self.assertRaises(sources.LeaderboardError):
                sources.fetch_leaderboard("trending")

    def test_classement_inconnu_refuse(self):
        with self.assertRaises(ValueError):
            sources.fetch_leaderboard("../admin")


if __name__ == "__main__":
    unittest.main()
