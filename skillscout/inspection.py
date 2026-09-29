"""Inspection GitHub complète d'un candidat, jusqu'à l'évaluation de confiance."""
from __future__ import annotations

import re

from . import github, trust


_FM_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_BLOCK_MARKERS = (">", "|", ">-", "|-", ">+", "|+")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> dict[str, str]:
    """Clés de premier niveau du frontmatter YAML d'un SKILL.md. Sous-ensemble
    volontaire (bibliothèque standard) : `clé: valeur`, guillemets, blocs
    `>` et `|`, suite indentée d'une valeur simple. Les clés imbriquées et les
    listes sont ignorées. {} si le frontmatter est absent ou non fermé."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
    if end is None:
        return {}
    out: dict[str, str] = {}
    key, block, style, plain = None, None, "", False
    for line in lines[1:end]:
        indented = line[:1] in (" ", "\t")
        if block is not None and (indented or not line.strip()):
            block.append(line.strip())
            continue
        if block is not None:
            out[key] = (" ".join(x for x in block if x) if style == ">"
                        else "\n".join(block).strip())
            block = None
        if indented and plain and out.get(key):
            out[key] += " " + line.strip()          # suite d'une valeur simple
            continue
        m = _FM_KEY.match(line)
        if not m:
            plain = False
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in _BLOCK_MARKERS:
            block, style, plain = [], value[0], False
            continue
        out[key] = _unquote(value)
        plain = bool(value) and value[0] not in "\"'"
    if block is not None:
        out[key] = " ".join(x for x in block if x) if style == ">" else "\n".join(block).strip()
    return out


def _read_skill_mds(source: str, md_paths: list[str], blobs: dict,
                    branch: str, cache: github.Cache) -> str | None:
    """Texte de tous les SKILL.md du skill, bout à bout, pour l'analyse. None
    si aucun n'est localisé ou si l'un d'eux est illisible : un texte partiel
    laisserait croire que tout a été vu."""
    texts = []
    for path in md_paths:
        sha = blobs.get(path)
        try:
            texts.append(github.fetch_blob(source, sha, cache) if sha
                         else github.fetch_skill_md(source, branch, path))
        except (github.GhError, OSError):
            return None
    return "\n\n".join(texts) if texts else None


def inspect_candidate(cand: dict, cache: github.Cache, now: float) -> dict:
    """Toute l'inspection GitHub d'un candidat : métadonnées, arbre restreint
    au skill, SKILL.md épinglés, évaluation. Lève GhError si `gh` échoue."""
    source = cand["source"]
    repo_meta = github.fetch_repo(source, cache)
    # L'éditeur s'identifie depuis la réponse de l'API (`owner_login`), jamais
    # depuis `source`. Si l'API ne l'a pas renvoyé, on n'interroge pas
    # `users/` avec une chaîne vide : `evaluate()` écartera le candidat.
    owner = repo_meta.get("owner_login") or ""
    owner_meta = github.fetch_owner(owner, cache) if owner else {}
    snap = github.fetch_tree_snapshot(source, cache, repo_meta.get("pushed_at", ""))
    paths = snap.get("paths", [])
    truncated = snap.get("truncated", True)
    scoped = trust.skill_paths(paths, cand["skill_id"])

    exec_bits = snap.get("exec_bits", [])
    opaque = snap.get("opaque_entries", [])
    # Premier passage sur la seule structure (identité, troncature, fichiers,
    # entrées opaques) : inutile de lire le SKILL.md d'un candidat déjà écarté.
    row = trust.evaluate(cand, repo_meta, owner_meta, scoped, now, truncated,
                   exec_bits=exec_bits, opaque=opaque, check_text=False)
    body, md_path, md_sha = None, None, None
    if not row["excluded"]:
        blobs = snap.get("blobs", {})
        md_paths = trust.locate_skill_mds(paths, cand["skill_id"])
        if md_paths:
            md_path = trust.primary_skill_md(md_paths)
            md_sha = blobs.get(md_path)
        if set(md_paths) & set(opaque):
            # Le blob d'un SKILL.md en lien symbolique ne contient que le
            # chemin de sa cible : ce n'est pas le texte que l'agent suivra.
            full = None
        else:
            full = _read_skill_mds(source, md_paths, blobs,
                                   repo_meta.get("default_branch", "main"), cache)
        row = trust.evaluate(cand, repo_meta, owner_meta, scoped, now, truncated, full,
                       exec_bits=exec_bits, opaque=opaque)
        # Le texte complet est conservé : Jev le jugera en entier.
        body = full

    blobs = snap.get("blobs", {})
    row["skill_files"] = {p: blobs[p] for p in scoped if p in blobs}
    row["skill_md_paths"] = trust.locate_skill_mds(paths, cand["skill_id"])
    row["exec_bits_in_scope"] = [p for p in scoped if p in set(exec_bits)]
    row["opaque_in_scope"] = [p for p in scoped if p in set(opaque)]
    fm = parse_frontmatter(body) if body else {}
    row["description"] = fm.get("description", "")
    row["md_name"] = fm.get("name", "")
    row["body"] = body
    row["skill_md_path"] = md_path
    row["skill_md_sha"] = md_sha
    row["tree_sha"] = snap.get("sha", "")
    # `evaluate` n'exclut pas un éditeur de confiance pour une arborescence
    # tronquée ; la routine, elle, en a besoin (liste de fichiers incomplète).
    row["truncated"] = truncated
    row["default_branch"] = repo_meta.get("default_branch", "main")
    return row
