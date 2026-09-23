"""Classement final des candidats évalués."""
from __future__ import annotations


def rank(evaluated: list[dict], top: int = 10) -> list[dict]:
    """Trie par score décroissant ; à score égal, départage par
    `relevance_rank` croissant (le mieux classé par skills.sh d'abord). La
    pertinence n'est jamais un terme du score — seulement un départage."""
    kept = [e for e in evaluated if not e["excluded"]]
    kept.sort(key=lambda e: (-e["score"], e.get("relevance_rank", 0)))
    return kept[:top]
