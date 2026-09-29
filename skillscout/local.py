"""Skills déjà présents dans ~/.claude/skills : lecture seule, jamais d'écriture."""
from __future__ import annotations

import re
from pathlib import Path

from . import github

# Toujours avec `fullmatch` : `re.match` et `$` acceptent un "\n" final.
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul",
                     *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _reserved(segment: str) -> bool:
    return segment.split(".")[0].lower() in _WINDOWS_RESERVED or segment.endswith(".")


def is_safe_name(name: str) -> bool:
    """Nom de dossier sous ~/.claude/skills. `skill_id` vient de skills.sh,
    pas de nous : aucun chemin n'est construit sans passer ce filtre."""
    return bool(_SAFE_NAME.fullmatch(name)) and not _reserved(name)


def local_status(row: dict, skills_dir: Path) -> str | None:
    """Ce skill est-il déjà dans `skills_dir` ? « same » si un dossier à son
    nom (identifiant skills.sh ou `name` du frontmatter) contient un SKILL.md
    de même empreinte Git que l'un des SKILL.md évalués (un dépôt peut en
    contenir plusieurs : original, traductions), « other » si un dossier à ce
    nom existe avec un autre contenu, None sinon. Le nom seul ne suffit pas à
    dire « déjà installé » : deux skills différents portent souvent le même.
    Les fins de ligne CRLF sont ramenées à LF avant comparaison : `npx skills`
    sous Windows peut les avoir converties."""
    files = row.get("skill_files") or {}
    shas = {files[p] for p in row.get("skill_md_paths") or () if p in files}
    shas.discard(None)
    if row.get("skill_md_sha"):
        shas.add(row["skill_md_sha"])
    found = False
    for name in dict.fromkeys(n.lower() for n in (row.get("skill_id"), row.get("md_name")) if n):
        if not is_safe_name(name):        # vient de skills.sh : jamais un chemin
            continue
        folder = skills_dir / name
        try:
            data = (folder / "SKILL.md").read_bytes()
        except OSError:
            found = found or folder.exists()
            continue
        found = True
        if {github.git_blob_sha(data), github.git_blob_sha(data.replace(b"\r\n", b"\n"))} & shas:
            return "same"
    return "other" if found else None
