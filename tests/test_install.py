import json, os, shutil, stat, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import config, github, install

NOW = "2026-09-28T10:00:00Z"


def blobs(**files):
    """{rel: texte} -> (octets par rel, sha attendu par rel)."""
    data = {rel.replace("__", "/").replace("_md", ".md"): txt.encode() for rel, txt in files.items()}
    return data, {rel: github.git_blob_sha(b) for rel, b in data.items()}


def row(name="tdd", **kw):
    return dict(skill_id=name, source="obra/superpowers", tree_sha="t" * 40, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.paths = config.Paths.under(self.root)
        self.paths.skills_dir.mkdir(parents=True)

    def install(self, name="tdd", run_id="r1", **files):
        data, expected = blobs(**(files or {"SKILL_md": "# tdd\n", "refs__a_md": "a\n"}))
        return install.install_skill(row(name), data, expected, self.paths,
                                     run_id=run_id, scores={"meta": 3.0}, now=NOW)


class TestNoms(unittest.TestCase):
    def test_noms_surs(self):
        for ok in ("tdd", "code-review.v2", "a", "x_1"):
            self.assertTrue(install.is_safe_name(ok), ok)
        for bad in ("", "../x", "a/b", "A", "con", "nul.md", "x.", "-x", "a" * 65, "a\\b"):
            self.assertFalse(install.is_safe_name(bad), bad)

    def test_chemins_relatifs_surs(self):
        for ok in ("SKILL.md", "refs/a.md", "LICENSE"):
            self.assertTrue(install.is_safe_relpath(ok), ok)
        for bad in ("../x.md", "/x.md", "a\\b.md", "C:x.md", "aux.md", "a/./b.md", "a//b.md", ""):
            self.assertFalse(install.is_safe_relpath(bad), bad)

    def test_retour_a_la_ligne_final_refuse(self):
        # Revue finale I8 : `re.match` avec `$` acceptait un "\n" final.
        self.assertFalse(install.is_safe_name("tdd\n"))
        self.assertFalse(install.is_safe_relpath("sub\n/x.md"))
        self.assertFalse(install.is_safe_relpath("x.md\n"))

    def test_fichiers_texte(self):
        for ok in ("SKILL.md", "refs/a.TXT", "LICENSE", "notice"):
            self.assertTrue(install.is_text_file(ok), ok)
        for bad in ("run.sh", "a.json", "LICENSE.sh", "img.png"):
            self.assertFalse(install.is_text_file(bad), bad)

    def test_relative_files(self):
        r = {"skill_md_paths": ["skills/tdd/SKILL.md"],
             "skill_files": {"skills/tdd/SKILL.md": "1", "skills/tdd/refs/a.md": "2"}}
        self.assertEqual(install.relative_files(r), {"SKILL.md": "1", "refs/a.md": "2"})
        self.assertIsNone(install.relative_files({"skill_md_paths": ["SKILL.md"], "skill_files": {}}))
        self.assertIsNone(install.relative_files(
            {"skill_md_paths": ["a/tdd/SKILL.md", "b/tdd/SKILL.md"], "skill_files": {}}))


class TestInstall(Base):
    def test_ecrit_les_octets_exacts_et_le_manifeste(self):
        target = self.install()
        self.assertEqual(target, self.paths.skills_dir / "tdd")
        self.assertEqual((target / "SKILL.md").read_bytes(), b"# tdd\n")
        self.assertEqual((target / "refs" / "a.md").read_bytes(), b"a\n")
        [entry] = install.installed(self.paths)
        self.assertEqual(entry["name"], "tdd")
        self.assertEqual(entry["run_id"], "r1")
        self.assertEqual(entry["files"]["SKILL.md"], github.git_blob_sha(b"# tdd\n"))
        self.assertEqual(entry["installed_at"], NOW)
        self.assertEqual(list(self.paths.staging_dir.iterdir()), [])

    def test_empreinte_fausse_rien_n_est_ecrit(self):
        data, expected = blobs(SKILL_md="# tdd\n")
        expected["SKILL.md"] = "0" * 40
        with self.assertRaises(install.InstallError):
            install.install_skill(row(), data, expected, self.paths, run_id="r", scores={}, now=NOW)
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertEqual(install.installed(self.paths), [])

    def test_n_ecrase_jamais(self):
        (self.paths.skills_dir / "tdd").mkdir()
        (self.paths.skills_dir / "tdd" / "SKILL.md").write_text("à moi", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            self.install()
        self.assertEqual((self.paths.skills_dir / "tdd" / "SKILL.md").read_text(encoding="utf-8"),
                         "à moi")

    def test_nom_pris_dans_le_registre_npx(self):
        self.paths.skill_lock.parent.mkdir(parents=True)
        self.paths.skill_lock.write_text(json.dumps({"version": 3, "skills": {"TDD": {}}}),
                                         encoding="utf-8")
        with self.assertRaises(install.InstallError):
            self.install()

    def test_fichier_non_texte_ou_chemin_dangereux(self):
        for files in ({"SKILL_md": "x", "run.sh": "echo"}, {"SKILL_md": "x", "..__evil_md": "x"}):
            data, expected = blobs(**files)
            with self.assertRaises(install.InstallError, msg=str(files)):
                install.install_skill(row(), data, expected, self.paths, run_id="r",
                                      scores={}, now=NOW)
        self.assertFalse((self.paths.skills_dir / "tdd").exists())

    def test_trop_gros(self):
        data, expected = blobs(SKILL_md="x" * (install.MAX_TOTAL_BYTES + 1))
        with self.assertRaises(install.InstallError):
            install.install_skill(row(), data, expected, self.paths, run_id="r", scores={}, now=NOW)

    def test_manifeste_inscriptible_sinon_annulation(self):
        with patch("skillscout.install._write_manifest", side_effect=OSError("disque plein")):
            with self.assertRaises(install.InstallError):
                self.install()
        self.assertFalse((self.paths.skills_dir / "tdd").exists())

    def test_manifeste_corrompu_bloque(self):
        self.paths.config_dir.mkdir(parents=True)
        self.paths.manifest.write_text("{pas du json", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            self.install()
        self.assertFalse((self.paths.skills_dir / "tdd").exists())

    def test_present_names(self):
        (self.paths.skills_dir / "Existant").mkdir()
        self.install(name="neuf")
        self.assertEqual(install.present_names(self.paths), {"existant", "neuf"})

    # -- Fix round 1 : le rollback doit couvrir toute erreur, pas seulement OSError/InstallError --

    def test_scores_non_serialisables_rien_n_est_ecrit(self):
        data, expected = blobs(SKILL_md="# tdd\n")
        with self.assertRaises(install.InstallError):
            install.install_skill(row(), data, expected, self.paths, run_id="r",
                                  scores={"k": {1, 2}}, now=NOW)  # un set : json.dumps refuse
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertEqual(install.installed(self.paths), [])
        self.assertFalse(self.paths.staging_dir.exists())

    def test_source_absente_rien_n_est_ecrit(self):
        data, expected = blobs(SKILL_md="# tdd\n")
        bad_row = {"skill_id": "tdd", "tree_sha": "t" * 40}  # pas de "source"
        with self.assertRaises(install.InstallError):
            install.install_skill(bad_row, data, expected, self.paths, run_id="r",
                                  scores={}, now=NOW)
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertEqual(install.installed(self.paths), [])
        self.assertFalse(self.paths.staging_dir.exists())

    def test_manifeste_leve_une_erreur_non_os_annule(self):
        with patch("skillscout.install._write_manifest", side_effect=RuntimeError("boom")):
            with self.assertRaises(install.InstallError):
                self.install()
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertEqual(install.installed(self.paths), [])

    # -- Fix round 1 : deux chemins qui ne diffèrent que par la casse --

    def test_collision_de_casse_refusee(self):
        cas = ({"SKILL_md": "x", "refs__a_md": "a", "REFS__b_md": "b"},
               {"SKILL_md": "x", "skill_md": "y"})
        for files in cas:
            data, expected = blobs(**files)
            with self.assertRaises(install.InstallError, msg=str(files)):
                install.install_skill(row(), data, expected, self.paths, run_id="r",
                                      scores={}, now=NOW)
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertFalse(self.paths.staging_dir.exists())


class TestUninstall(Base):
    def test_retire_ce_qui_a_ete_installe(self):
        target = self.install()
        self.assertEqual(install.uninstall("tdd", self.paths), target)
        self.assertFalse(target.exists())
        self.assertEqual(install.installed(self.paths), [])

    def test_un_skill_hors_manifeste_survit(self):
        mine = self.paths.skills_dir / "perso"
        mine.mkdir()
        (mine / "SKILL.md").write_text("# perso", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            install.uninstall("perso", self.paths)
        self.assertTrue((mine / "SKILL.md").exists())

    def test_dossier_modifie_refuse(self):
        target = self.install()
        (target / "SKILL.md").write_text("modifié par l'utilisateur", encoding="utf-8")
        with self.assertRaises(install.InstallError):
            install.uninstall("tdd", self.paths)
        self.assertTrue(target.exists())

    def test_dossier_deja_supprime_nettoie_le_manifeste(self):
        target = self.install()
        shutil.rmtree(target)
        install.uninstall("tdd", self.paths)
        self.assertEqual(install.installed(self.paths), [])

    def test_uninstall_last(self):
        self.install(name="vieux", run_id="2026-09-21T10:00:00Z")
        self.install(name="neuf1", run_id="2026-09-28T10:00:00Z")
        self.install(name="neuf2", run_id="2026-09-28T10:00:00Z")
        removed, errors = install.uninstall_last(self.paths)
        self.assertEqual(sorted(p.name for p in removed), ["neuf1", "neuf2"])
        self.assertEqual(errors, [])
        self.assertEqual([e["name"] for e in install.installed(self.paths)], ["vieux"])
        removed, _ = install.uninstall_last(self.paths)
        self.assertEqual([p.name for p in removed], ["vieux"])
        self.assertEqual(install.uninstall_last(self.paths), ([], []))

    # -- Fix round 1 : désinstallation atomique (déplacement hors de skills_dir) --

    def test_desinstalle_un_fichier_en_lecture_seule(self):
        target = self.install()
        skill_md = target / "SKILL.md"
        skill_md.chmod(stat.S_IREAD)
        self.addCleanup(lambda: skill_md.chmod(stat.S_IWRITE) if skill_md.exists() else None)
        result = install.uninstall("tdd", self.paths)
        self.assertEqual(result, target)
        self.assertFalse(target.exists())
        self.assertEqual(install.installed(self.paths), [])

    def test_deplacement_hors_de_skills_echoue(self):
        target = self.install()
        with patch("skillscout.install.os.rename", side_effect=OSError("verrouillé")):
            with self.assertRaises(install.InstallError):
                install.uninstall("tdd", self.paths)
        self.assertTrue(target.exists())
        self.assertEqual((target / "SKILL.md").read_bytes(), b"# tdd\n")
        self.assertEqual((target / "refs" / "a.md").read_bytes(), b"a\n")
        [entry] = install.installed(self.paths)
        self.assertEqual(entry["name"], "tdd")

    def test_uninstall_last_continue_apres_un_echec(self):
        self.install(name="bon", run_id="r9")
        mauvais = self.install(name="mauvais", run_id="r9")
        (mauvais / "SKILL.md").write_text("modifié après coup", encoding="utf-8")
        removed, errors = install.uninstall_last(self.paths)
        self.assertEqual([p.name for p in removed], ["bon"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(mauvais.exists())
        self.assertEqual([e["name"] for e in install.installed(self.paths)], ["mauvais"])


def entry(name="tdd", **over):
    e = {"name": name, "source": "o/r", "skill_id": name, "tree_sha": "t" * 40,
         "files": {"SKILL.md": "1" * 40}, "installed_at": NOW, "run_id": "r1", "scores": {}}
    e.update(over)
    return e


class TestManifesteValide(Base):
    """Revue finale I8 : une entrée de manifeste n'est jamais crue sur parole."""

    def write_manifest(self, skills):
        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        self.paths.manifest.write_text(json.dumps({"version": 1, "skills": skills}),
                                       encoding="utf-8")

    def victim(self):
        victim = self.paths.claude_dir / "victim"
        victim.mkdir()
        (victim / "SKILL.md").write_bytes(b"# precieux\n")
        return victim, entry("../victim", skill_id="victim",
                             files={"SKILL.md": github.git_blob_sha(b"# precieux\n")})

    def test_entree_forgee_hors_de_skills_refusee_victime_intacte(self):
        victim, forged = self.victim()
        self.write_manifest([forged])
        with self.assertRaises(install.InstallError) as ctx:
            install.uninstall("../victim", self.paths)
        self.assertIn("manifeste invalide", str(ctx.exception))
        self.assertEqual((victim / "SKILL.md").read_bytes(), b"# precieux\n")

    def test_cible_hors_de_skills_refusee_meme_si_le_manifeste_passait(self):
        victim, forged = self.victim()
        with patch("skillscout.install.read_manifest",
                   return_value={"version": 1, "skills": [forged]}):
            with self.assertRaises(install.InstallError):
                install.uninstall("../victim", self.paths)
        self.assertEqual((victim / "SKILL.md").read_bytes(), b"# precieux\n")

    def test_entrees_mal_formees(self):
        cas = ["pas un objet", {"source": "o/r"}, entry(name="tdd\n"), entry(name=3),
               entry(files=["SKILL.md"]), entry(files={"../x.md": "1" * 40}),
               entry(files={"SKILL.md": 5}), entry(tree_sha=None), entry(run_id=3),
               entry(installed_at=None), entry(source=["o/r"]), entry(skill_id=None),
               entry(scores=[])]
        for bad in cas:
            self.write_manifest([bad])
            for call in (install.installed, install.present_names,
                         lambda p: install.uninstall("tdd", p)):
                with self.assertRaises(install.InstallError, msg=repr(bad)) as ctx:
                    call(self.paths)
                self.assertIn("manifeste invalide", str(ctx.exception), repr(bad))

    def test_entree_valide_acceptee(self):
        self.write_manifest([entry()])
        self.assertEqual([e["name"] for e in install.installed(self.paths)], ["tdd"])


class TestRenommageRelance(Base):
    """Revue finale I9 : refus d'accès passager de Windows (WinError 5) sur
    le renommage, dû à un antivirus ou à l'indexeur qui tient un fichier."""

    @staticmethod
    def flaky(fails):
        real, calls = os.rename, []

        def rename(src, dst):
            calls.append((src, dst))
            if len(calls) <= fails:
                raise PermissionError(13, "Accès refusé")
            return real(src, dst)
        return rename, calls

    def test_installation_relancee_apres_un_refus_passager(self):
        rename, calls = self.flaky(1)
        with patch("skillscout.install.os.rename", side_effect=rename), \
             patch("skillscout.install.time.sleep") as sleep:
            target = self.install()
        self.assertEqual((target / "SKILL.md").read_bytes(), b"# tdd\n")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(0.05)
        self.assertEqual([e["name"] for e in install.installed(self.paths)], ["tdd"])

    def test_installation_abandonnee_apres_cinq_refus(self):
        with patch("skillscout.install.os.rename",
                   side_effect=PermissionError(13, "Accès refusé")) as ren, \
             patch("skillscout.install.time.sleep") as sleep:
            with self.assertRaises(install.InstallError):
                self.install()
        self.assertEqual(ren.call_count, 5)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.05, 0.1, 0.2, 0.4])
        self.assertFalse((self.paths.skills_dir / "tdd").exists())
        self.assertEqual(install.installed(self.paths), [])
        self.assertEqual(list(self.paths.staging_dir.iterdir()), [])

    def test_cible_existante_jamais_relancee(self):
        with patch("skillscout.install.os.rename",
                   side_effect=FileExistsError(17, "existe")) as ren, \
             patch("skillscout.install.time.sleep") as sleep:
            with self.assertRaises(install.InstallError):
                self.install()
        self.assertEqual(ren.call_count, 1)
        sleep.assert_not_called()

    def test_desinstallation_relancee_apres_un_refus_passager(self):
        target = self.install()
        rename, calls = self.flaky(1)
        with patch("skillscout.install.os.rename", side_effect=rename), \
             patch("skillscout.install.time.sleep"):
            install.uninstall("tdd", self.paths)
        self.assertEqual(len(calls), 2)
        self.assertFalse(target.exists())
        self.assertEqual(install.installed(self.paths), [])

    def test_desinstallation_abandonnee_apres_cinq_refus(self):
        target = self.install()
        with patch("skillscout.install.os.rename",
                   side_effect=PermissionError(13, "Accès refusé")) as ren, \
             patch("skillscout.install.time.sleep"):
            with self.assertRaises(install.InstallError):
                install.uninstall("tdd", self.paths)
        self.assertEqual(ren.call_count, 5)
        self.assertEqual((target / "SKILL.md").read_bytes(), b"# tdd\n")
        self.assertEqual([e["name"] for e in install.installed(self.paths)], ["tdd"])


if __name__ == "__main__":
    unittest.main()
