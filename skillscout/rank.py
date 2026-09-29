"""Classement final des candidats évalués, et regroupement des versions d'un
même skill (forks, traductions, copies)."""
from __future__ import annotations

NEED_MIN = 1.5      # pertinence Jev au besoin, sur 3, pour être affiché (0,5 ramené à l'échelle 0–3)
MAX_PROMOTIONS = 2  # versions de secours jugées par nom quand la première est rejetée


def _group_rank(e: dict) -> int:
    return e.get("group_rank", e.get("relevance_rank", 0))


def rank(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Classement sans Jev : ordre de pertinence de skills.sh (meilleur rang
    du groupe), puis score de confiance, puis installations. La confiance
    n'ordonne plus : elle écarte (`excluded`) et s'affiche."""
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: (_group_rank(e), -e["score"], -e.get("installs", 0)))
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
    kept.sort(key=lambda e: (-need_of(e), _group_rank(e), -e["score"]))
    return kept[:top]


def _safest(e: dict) -> tuple:
    return (e["excluded"], -e["score"], e.get("relevance_rank", 0))


def _swap(rows: list[dict], old: dict, new: dict) -> None:
    """`new` (nouvelle venue ou membre du groupe) remplace `old`, qui devient
    membre du groupe avec son éventuelle exclusion."""
    new["_members"] = [m for m in old.pop("_members", []) if m is not new] + [old]
    new["group_rank"] = min(old.pop("group_rank", _group_rank(old)),
                            new.get("relevance_rank", 0))
    new["_tries"] = old.pop("_tries", 0)
    rows[next(i for i, x in enumerate(rows) if x is old)] = new


def add_deduplicated(rows: list[dict], new: list[dict]) -> list[dict]:
    """Ajoute `new` à `rows` en ne gardant qu'une ligne par nom de skill :
    les forks et traductions d'un même skill remplissaient sinon le top 10.
    La ligne gardée est la plus sûre (non écartée d'abord, puis meilleur score
    de confiance, puis meilleur rang) ; les autres versions restent entières
    dans `_members`, avec leur exclusion éventuelle, et `group_rank` porte le
    meilleur rang du groupe. Renvoie les lignes réellement ajoutées, les
    seules à faire juger par Jev. Une ligne gardée n'est remplacée ici que si
    elle était écartée ; après un rejet par Jev, `promote` prend le relais."""
    index = {e["skill_id"].lower(): e for e in rows}
    added: list[dict] = []
    for e in sorted(new, key=_safest):
        key = e["skill_id"].lower()
        kept = index.get(key)
        if kept is None:
            e["_members"], e["group_rank"] = [], e.get("relevance_rank", 0)
            rows.append(e)
            added.append(e)
            index[key] = e
        elif kept["excluded"] and not e["excluded"]:
            _swap(rows, kept, e)
            index[key] = e
            added.append(e)
        else:
            kept["_members"].append(e)
            kept["group_rank"] = min(kept["group_rank"], e.get("relevance_rank", 0))
    return added


def promote(rows: list[dict]) -> list[dict]:
    """Pour chaque nom dont la ligne gardée a été rejetée par Jev (écartée ou
    jugée non pertinente), la version suivante la plus sûre, non écartée et
    non encore jugée, prend sa place, au plus MAX_PROMOTIONS fois par nom.
    Une copie à l'identique d'une version déjà jugée n'est pas promue : elle
    aurait le même verdict. Renvoie les lignes promues, à faire juger."""
    out = []
    for kept in list(rows):
        rejected = kept["excluded"] or 0.0 <= need_of(kept) < NEED_MIN
        if not rejected or kept.get("_tries", 0) >= MAX_PROMOTIONS:
            continue
        group = [kept] + kept["_members"]
        judged = {m.get("skill_md_sha") for m in group if "jev" in m} - {None}
        spare = [m for m in kept["_members"] if not m["excluded"] and "jev" not in m
                 and m.get("skill_md_sha") not in judged]
        if not spare:
            continue
        best = min(spare, key=_safest)
        tries = kept.get("_tries", 0) + 1
        _swap(rows, kept, best)
        best["_tries"] = tries
        out.append(best)
    return out


def members(rows: list[dict]):
    """Toutes les versions examinées : lignes gardées et membres de leur groupe."""
    for e in rows:
        yield e
        yield from e.get("_members", ())


def describe_group(kept: dict) -> None:
    """Pose sur une ligne affichée ses autres versions : `copies` (même
    SKILL.md) et `variants` (contenu différent) parmi les versions non
    écartées, et `excluded_versions` pour les autres, qui ne sont jamais
    présentées comme des alternatives."""
    sha = kept.get("skill_md_sha")
    ok = [m for m in kept.get("_members", ()) if not m["excluded"]]
    kept["copies"] = [m["source"] for m in ok if sha and m.get("skill_md_sha") == sha]
    kept["variants"] = [m["source"] for m in ok if not (sha and m.get("skill_md_sha") == sha)]
    kept["excluded_versions"] = [{"source": m["source"], "reason": m.get("reason")}
                                 for m in kept.get("_members", ()) if m["excluded"]]
