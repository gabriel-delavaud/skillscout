"""Après le top 5 : choisir des skills, les installer, vérifier, puis lancer la commande
« après installation » (par exemple la mise à jour d'un catalogue personnel)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import local

ENV_APRES = "SKILLSCOUT_APRES_INSTALLATION"
QUESTION = "Lesquels installer ? (ex. 1,3 · « tout » · Entrée pour aucun) : "
_TOUT = {"tout", "tous", "all", "*"}
# `source` et `skill_id` viennent de skills.sh : jamais passés à npx sans ce filtre
# (sous Windows, npx est un .cmd et cmd.exe interprète certains caractères).
_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")


def parse_choice(texte: str, n: int) -> list[int] | None:
    """Numéros choisis (1..n, sans doublon, dans l'ordre tapé) ; [] pour aucun,
    None si la réponse n'est pas comprise."""
    texte = texte.strip().lower()
    if texte in ("", "0"):
        return []
    if texte in _TOUT:
        return list(range(1, n + 1))
    choix = []
    for morceau in re.split(r"[\s,;]+", texte):
        if not morceau.isdigit() or not 1 <= int(morceau) <= n:
            return None
        if int(morceau) not in choix:
            choix.append(int(morceau))
    return choix


def demander_choix(rows: list[dict], lire=input, afficher=print) -> list[dict]:
    """Pose la question jusqu'à une réponse comprise ; fin de saisie = aucun."""
    while True:
        try:
            reponse = lire("\n" + QUESTION)
        except EOFError:
            return []
        choix = parse_choice(reponse, len(rows))
        if choix is not None:
            return [rows[i - 1] for i in choix]
        afficher(f"Réponse non comprise : tapez des numéros entre 1 et {len(rows)}, "
                 "« tout », ou Entrée pour aucun.")


def confirmer(question: str, lire=input) -> bool:
    try:
        return lire(question).strip().lower() in ("o", "oui", "y", "yes")
    except EOFError:
        return False


def noms_npx(home: Path | None = None) -> set[str]:
    """Skills installés par `npx skills` d'après son registre ; les autres dossiers
    de ~/.claude/skills (skills écrits à la main, clonés à la main) n'y sont pas."""
    chemin = (home or Path.home()) / ".agents" / ".skill-lock.json"
    try:
        return set(json.loads(chemin.read_text(encoding="utf-8")).get("skills", {}))
    except (OSError, ValueError, AttributeError):
        return set()


def _dossiers(row: dict, skills_dir: Path) -> list[str]:
    """Noms de dossier existants sous skills_dir pour ce skill (id skills.sh ou `name`)."""
    noms = dict.fromkeys(n.lower() for n in (row.get("skill_id"), row.get("md_name")) if n)
    return [n for n in noms if local.is_safe_name(n) and (skills_dir / n).exists()]


class Installeur:
    """Installe les skills choisis avec `npx skills`, Claude Code seulement.

    `lancer` exécute une commande (liste d'arguments) et rend un CompletedProcess ;
    `lire` pose une question oui/non. Tous deux sont remplaçables pour les tests."""

    def __init__(self, skills_dir: Path, npx: str, installes_npx: set[str],
                 lancer=subprocess.run, lire=input):
        self.skills_dir, self.npx, self.installes_npx = skills_dir, npx, installes_npx
        self.lancer, self.lire = lancer, lire

    def _npx(self, *arguments: str) -> int:
        # --yes : sans lui, npx demande « Ok to proceed? » la première fois qu'il
        # télécharge l'outil `skills`, au milieu de l'installation.
        return self.lancer([self.npx, "--yes", "skills", *arguments]).returncode

    def installer(self, row: dict) -> tuple[str, str]:
        """(état, message). États : installe, a_jour, refuse, echec, annule."""
        nom, source = row.get("skill_id") or "", row.get("source") or ""
        if not (local.is_safe_name(nom.lower()) and _SOURCE.fullmatch(source)):
            return "echec", f"{nom} : nom ou dépôt inattendu ({source}), installation refusée."
        if not row.get("skill_md_sha"):
            return "refuse", (f"{nom} : son SKILL.md n'a pas été lu, l'installation ne pourrait "
                              "pas être vérifiée. Installez-le à la main si vous lui faites confiance.")

        etat_local = row.get("installed")
        if etat_local == "same":
            return "a_jour", f"{nom} : déjà installé, identique — rien à faire."
        if etat_local in ("other", "variant"):
            existants = _dossiers(row, self.skills_dir)
            hors_npx = [d for d in existants if d not in self.installes_npx]
            if hors_npx:
                return "refuse", (f"{nom} : un skill « {hors_npx[0]} » installé sans npx skills "
                                  "(écrit ou cloné à la main) existe déjà ; il n'est jamais remplacé.")
            if not confirmer(f"Un autre skill « {nom} » est déjà installé. Le remplacer ? [o/N] ",
                             self.lire):
                return "refuse", f"{nom} : conservé tel quel."

        if self._npx("add", source, "--skill", nom, "-g", "-a", "claude-code", "-y") != 0:
            return "echec", f"{nom} : npx skills add a échoué."
        if local.local_status(row, self.skills_dir) == "same":
            return "installe", f"{nom} : installé depuis {source}."
        # Le dépôt a changé entre l'inspection et l'installation : ce texte n'a pas été vérifié.
        for dossier in _dossiers(row, self.skills_dir) or [nom]:
            self._npx("remove", dossier, "-g", "-a", "claude-code", "-y")
        return "annule", (f"{nom} : le SKILL.md installé n'est pas celui inspecté (le dépôt a "
                          "changé depuis) ; désinstallé. Relancez la recherche pour l'évaluer à nouveau.")


def lancer_apres(commande: str, lancer=subprocess.run) -> int:
    """Commande « après installation », telle que l'utilisateur l'a écrite (shell),
    sortie affichée directement dans le terminal."""
    return lancer(commande, shell=True).returncode


def installer_choix(rows: list[dict], skills_dir: Path, *, lire=input, afficher=print,
                    lancer=subprocess.run, environ=os.environ, which=shutil.which,
                    home: Path | None = None) -> int:
    """Question, installations, puis commande « après installation ». Rend 0 si tout
    s'est bien passé (ou si rien n'a été choisi), 1 sinon."""
    choisis = demander_choix(rows, lire, afficher)
    if not choisis:
        return 0
    npx = which("npx")
    if not npx:
        afficher("npx est introuvable : installez Node.js (https://nodejs.org), puis "
                 "relancez la recherche.")
        return 1
    installeur = Installeur(skills_dir, npx, noms_npx(home), lancer, lire)
    installes, problemes = [], 0
    for row in choisis:
        etat, message = installeur.installer(row)
        afficher(("✓ " if etat in ("installe", "a_jour") else "✗ ") + message)
        installes += [row["skill_id"]] if etat == "installe" else []
        problemes += etat in ("echec", "annule")
    if not installes:
        return 1 if problemes else 0

    commande = (environ.get(ENV_APRES) or "").strip()
    if not commande:
        afficher(f"\n{len(installes)} skill(s) installé(s). Aucune commande après installation "
                 f"(variable {ENV_APRES}) : pensez à mettre à jour votre catalogue.")
        return 1 if problemes else 0
    afficher(f"\nAprès installation : {commande}")
    code = lancer_apres(commande, lancer)
    if code != 0:
        afficher(f"La commande après installation a échoué (code {code}). Les skills restent "
                 f"installés ; relancez-la une fois le problème réglé : {commande}")
        return 1
    return 1 if problemes else 0
