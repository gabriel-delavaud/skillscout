"""Inspection GitHub complète d'un candidat, jusqu'à l'évaluation de confiance."""
from __future__ import annotations

from . import github, trust


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
            md_path, md_sha = md_paths[0], blobs.get(md_paths[0])
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

    row["body"] = body
    row["skill_md_path"] = md_path
    row["skill_md_sha"] = md_sha
    row["tree_sha"] = snap.get("sha", "")
    row["default_branch"] = repo_meta.get("default_branch", "main")
    return row
