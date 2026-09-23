import base64, contextlib, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import github, inspection

HELLO = b"hello\n"
HELLO_SHA = "ce013625030ba8dba906f756967f9e9ca394464a"   # git hash-object de "hello\n"


class TestParseFrontmatter(unittest.TestCase):
    def test_cles_simples_et_guillemets(self):
        text = ('---\nname: tdd\ndescription: "Use when \\"testing\\""\n'
                "license: 'It''s MIT'\n---\n# corps\n")
        self.assertEqual(inspection.parse_frontmatter(text),
                         {"name": "tdd", "description": 'Use when "testing"',
                          "license": "It's MIT"})

    def test_blocs_plie_et_litteral(self):
        text = ("---\nname: a\ndescription: >\n  Use when\n  planning.\n"
                "notes: |\n  ligne 1\n  ligne 2\n---\n")
        fm = inspection.parse_frontmatter(text)
        self.assertEqual(fm["description"], "Use when planning.")
        self.assertEqual(fm["notes"], "ligne 1\nligne 2")

    def test_suite_de_valeur_indentee(self):
        text = "---\nname: a\ndescription: Use when\n  something breaks\n---\n"
        self.assertEqual(inspection.parse_frontmatter(text)["description"],
                         "Use when something breaks")

    def test_cles_imbriquees_ignorees(self):
        text = "---\nname: a\nmetadata:\n  type: x\n---\n"
        fm = inspection.parse_frontmatter(text)
        self.assertEqual(fm, {"name": "a", "metadata": ""})

    def test_absent_ou_non_ferme(self):
        self.assertEqual(inspection.parse_frontmatter("# pas de frontmatter"), {})
        self.assertEqual(inspection.parse_frontmatter("---\nname: a\n"), {})
        self.assertEqual(inspection.parse_frontmatter(""), {})

    def test_bom_et_crlf(self):
        text = "﻿---\r\nname: a\r\ndescription: b\r\n---\r\n"
        self.assertEqual(inspection.parse_frontmatter(text), {"name": "a", "description": "b"})


class TestBlobBytes(unittest.TestCase):
    def setUp(self):
        self.cache = github.Cache(os.path.join(tempfile.mkdtemp(), "c.db"))

    def test_git_blob_sha(self):
        self.assertEqual(github.git_blob_sha(HELLO), HELLO_SHA)

    def test_octets_verifies_et_mis_en_cache(self):
        b64 = base64.b64encode(HELLO).decode()
        raw = {"encoding": "base64", "content": b64[:4] + "\n" + b64[4:]}   # GitHub coupe les lignes
        with patch("skillscout.github.gh_json", return_value=raw) as g:
            self.assertEqual(github.fetch_blob_bytes("a/b", HELLO_SHA, self.cache), HELLO)
            self.assertEqual(github.fetch_blob_bytes("a/b", HELLO_SHA, self.cache), HELLO)
        self.assertEqual(g.call_count, 1)

    def test_empreinte_differente_refusee_et_non_cachee(self):
        raw = {"encoding": "base64", "content": base64.b64encode(b"autre\n").decode()}
        with patch("skillscout.github.gh_json", return_value=raw) as g:
            for _ in range(2):
                with self.assertRaises(github.GhError):
                    github.fetch_blob_bytes("a/b", HELLO_SHA, self.cache)
        self.assertEqual(g.call_count, 2)

    def test_encodage_ou_base64_invalide(self):
        for raw in ({"encoding": "utf-8", "content": "hello\n"},
                    {"encoding": "base64", "content": "abc"}):
            with patch("skillscout.github.gh_json", return_value=raw):
                with self.assertRaises(github.GhError):
                    github.fetch_blob_bytes("a/b", HELLO_SHA, self.cache)


class TestInspectionChampsNouveaux(unittest.TestCase):
    REPO = {"stars": 5, "pushed_at": "2026-09-01T00:00:00Z", "created_at": "2020-01-01T00:00:00Z",
            "owner_type": "Organization", "default_branch": "main",
            "full_name": "anthropics/skills", "owner_login": "anthropics"}
    OWNER = {"type": "Organization", "created_at": "2015-01-01T00:00:00Z",
             "public_repos": 50, "source_repos": 10}
    SNAP = {"sha": "t" * 40, "truncated": False,
            "paths": ["README.md", "skills/a/SKILL.md", "skills/a/ref.md", "skills/b/SKILL.md"],
            "blobs": {"README.md": "0" * 40, "skills/a/SKILL.md": "1" * 40,
                      "skills/a/ref.md": "2" * 40, "skills/b/SKILL.md": "3" * 40},
            "exec_bits": [], "opaque_entries": []}

    def _inspect(self, body):
        cache = github.Cache(os.path.join(tempfile.mkdtemp(), "c.db"))
        cand = {"skill_id": "a", "name": "a", "source": "anthropics/skills", "installs": 9,
                "relevance_rank": 0}
        with patch("skillscout.github.fetch_repo", return_value=self.REPO), \
             patch("skillscout.github.fetch_owner", return_value=self.OWNER), \
             patch("skillscout.github.fetch_tree_snapshot", return_value=self.SNAP), \
             patch("skillscout.github.fetch_blob", return_value=body):
            return inspection.inspect_candidate(cand, cache, 1789000000.0)

    def test_description_et_fichiers_du_skill(self):
        row = self._inspect("---\nname: a\ndescription: Use when planning\n---\n# a\n")
        self.assertEqual(row["description"], "Use when planning")
        self.assertEqual(row["md_name"], "a")
        self.assertEqual(row["skill_files"], {"skills/a/SKILL.md": "1" * 40,
                                              "skills/a/ref.md": "2" * 40})
        self.assertEqual(row["skill_md_paths"], ["skills/a/SKILL.md"])
        self.assertEqual(row["exec_bits_in_scope"], [])
        self.assertEqual(row["opaque_in_scope"], [])

    def test_sans_frontmatter(self):
        row = self._inspect("# a\nrien\n")
        self.assertEqual((row["description"], row["md_name"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
