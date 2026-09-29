"""Classement final des candidats évalués."""
from __future__ import annotations

NEED_MIN = 1.5      # pertinence Jev au besoin, sur 3, pour être affiché (0,5 ramené à l'échelle 0–3)


def rank(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Classement sans Jev : ordre de pertinence de skills.sh
    (`relevance_rank` croissant), puis score de confiance, puis installations.
    La confiance n'ordonne plus : elle écarte (`excluded`) et s'affiche."""
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: (e.get("relevance_rank", 0), -e["score"], -e.get("installs", 0)))
    return kept[:top]


def need_of(e: dict) -> float:
    """Pertinence Jev au besoin (0–3), ou -1 si le skill n'a pas été jugé."""
    j = e.get("jev")
    return j.relevance.get("need", -1.0) if j is not None and j.status == "ok" else -1.0


def is_relevant(e: dict) -> bool:
    return not e["excluded"] and need_of(e) >= NEED_MIN


def rank_with_jev(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Classement quand Jev a jugé : seuls les skills jugés pertinents
    (besoin ≥ NEED_MIN), par pertinence au besoin décroissante, puis rang
    skills.sh, puis confiance. Un skill non jugé n'est pas affiché : on ne
    sait pas s'il répond au besoin."""
    kept = [e for e in evaluated if is_relevant(e)]
    kept.sort(key=lambda e: (-need_of(e), e.get("relevance_rank", 0), -e["score"]))
    return kept[:top]


def _others(kept: dict, members: list[tuple[str, str | None]]) -> None:
    """Range les autres membres du groupe de `kept` : `copies` (même SKILL.md,
    octet pour octet) et `variants` (même nom, contenu différent)."""
    sha = kept.get("skill_md_sha")
    kept["copies"] = [src for src, s in members if sha and s == sha]
    kept["variants"] = [src for src, s in members if not (sha and s == sha)]


def add_deduplicated(rows: list[dict], new: list[dict]) -> list[dict]:
    """Ajoute `new` à `rows` en ne gardant qu'une ligne par nom de skill :
    les forks et traductions d'un même skill remplissaient sinon le top 10.
    La ligne gardée est la plus sûre (non écartée d'abord, puis meilleur score
    de confiance, puis meilleur rang) et prend le meilleur rang du groupe ;
    les autres sources vont dans `copies` (même SKILL.md) ou `variants`
    (contenu différent). Renvoie les lignes réellement ajoutées, les seules à
    faire juger par Jev : une variante écartée du groupe n'est ni jugée ni
    affichée. Une ligne déjà gardée n'est remplacée que si elle était
    écartée, pour ne pas facturer deux fois un jugement Jev."""
    index = {e["skill_id"].lower(): e for e in rows}
    added: list[dict] = []
    for e in sorted(new, key=lambda e: (e["excluded"], -e["score"], e.get("relevance_rank", 0))):
        key = e["skill_id"].lower()
        kept = index.get(key)
        if kept is None:
            rows.append(e)
            added.append(e)
            index[key] = e
            e["_members"] = []
            _others(e, e["_members"])
            continue
        best = min(e.get("relevance_rank", 0), kept.get("relevance_rank", 0))
        if kept["excluded"] and not e["excluded"]:
            e["_members"] = kept.pop("_members", []) + [(kept["source"], kept.get("skill_md_sha"))]
            kept.pop("copies", None)
            kept.pop("variants", None)
            rows[next(i for i, x in enumerate(rows) if x is kept)] = e
            added[:] = [x for x in added if x is not kept]
            added.append(e)
            index[key] = e
            kept = e
        else:
            kept["_members"].append((e["source"], e.get("skill_md_sha")))
        kept["relevance_rank"] = best
        _others(kept, kept["_members"])
    return added
