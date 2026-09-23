"""Classement final des candidats évalués."""
from __future__ import annotations


def rank(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Trie par score décroissant ; à score égal, départage par
    `relevance_rank` croissant (le mieux classé par skills.sh d'abord). La
    pertinence n'est jamais un terme du score — seulement un départage."""
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: (-e["score"], e.get("relevance_rank", 0)))
    return kept[:top]


def rank_with_jev(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Classement quand Jev a jugé : pertinence au besoin décroissante, puis
    score de confiance, puis installations. Un skill non jugé passe après
    tous les skills jugés, quel que soit son score."""
    def need(e: dict) -> float:
        j = e.get("jev")
        return j.relevance.get("need", -1.0) if j is not None and j.status == "ok" else -1.0
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: (-need(e), -e["score"], -e.get("installs", 0),
                             e.get("relevance_rank", 0)))
    return kept[:top]
