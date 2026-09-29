"""Forces et faiblesses d'un skill affiché, tirées de ce que skillscout a
mesuré : indicateurs du tri de confiance, notes de Jev et réponses aux
questions d'explication. Rien n'est rédigé par un modèle : chaque phrase
vient d'une règle ci-dessous, et la même mesure donne toujours la même phrase."""
from __future__ import annotations

import re

from . import rank

POPULAR = 10_000    # installations au-delà desquelles un skill est « très utilisé »
OBSCURE = 100       # … en deçà desquelles il est « peu utilisé »
YES, NO = 0.6, 0.25  # seuils de lecture d'une réponse Noul de Jev

_EXECS = re.compile(r"⚠ (\d+) fichiers? exécutables?")
_JEV = re.compile(r"⚠ Jev : (.+) (\d\.\d\d)")


def _from_flag(flag: str) -> tuple[str, str] | None:
    """(« + » ou « − », phrase) pour un indicateur du tri ou de l'affichage."""
    if flag == "éditeur en liste blanche":
        return "+", "éditeur reconnu (liste blanche)"
    if flag.startswith("organisation :"):
        return "+", "organisation établie (plus d'un an, 10 dépôts ou plus, active)"
    if flag == "sans fichier exécutable":
        return "+", "sans fichier exécutable : du texte seulement"
    if flag == "✓ déjà installé":
        return "+", "déjà installé chez vous"
    if flag.startswith("✓ variante installée"):
        return "+", flag[2:]
    if flag == "≈ autre version installée":
        return "−", "un autre skill de ce nom est déjà installé chez vous"
    if m := _EXECS.fullmatch(flag):
        return "−", f"contient {m.group(1)} fichier(s) exécutable(s)"
    if m := _JEV.fullmatch(flag):
        return "−", f"Jev y soupçonne : {m.group(1)} ({m.group(2)})"
    if flag.startswith("⚠ SKILL.md : "):
        return "−", "le texte demande : " + flag[len("⚠ SKILL.md : "):]
    if flag == "⚠ SKILL.md non lu":
        return "−", "texte non vérifié : SKILL.md illisible"
    if flag.startswith("⚠ non jugé par Jev"):
        return "−", flag[2:]
    if m := re.fullmatch(r"\+(\d+) copie\(s\)", flag):
        return "−", f"{m.group(1)} copie(s) identique(s) publiée(s) ailleurs (--json)"
    if m := re.fullmatch(r"\+(\d+) variante\(s\)", flag):
        return "−", f"{m.group(1)} version(s) retouchée(s) publiée(s) ailleurs (--json)"
    if flag.endswith("version(s) écartée(s)"):
        return "−", flag + " (--show-excluded)"
    if flag.startswith("⚠ "):
        return "−", flag[2:]
    return None


def strengths_weaknesses(row: dict) -> tuple[list[str], list[str]]:
    plus: list[str] = []
    minus: list[str] = []

    need = round(rank.need_of(row), 1)
    j = row.get("jev")
    if need >= 2.5:
        plus.append(f"répond exactement au besoin ({need:.1f}/3)")
    elif need >= 2.0:
        plus.append(f"répond clairement au besoin ({need:.1f}/3)")
    elif need >= 0:
        minus.append(f"ne répond qu'en partie au besoin ({need:.1f}/3)")
    if j is not None and j.status == "ok":
        substance, meta = round(j.relevance["substance"], 1), round(j.relevance["meta"], 1)
        severity = round(j.severity, 1)
        if substance >= 2.5:
            plus.append(f"méthode substantielle ({substance:.1f}/3)")
        elif substance < 1.5:
            minus.append(f"contenu mince ({substance:.1f}/3)")
        if meta >= 2.0:
            plus.append(f"améliore aussi la façon de travailler de Claude (méta {meta:.1f}/3)")
        if severity >= 1.5:
            minus.append(f"dégâts possibles s'il est suivi à la lettre (gravité {severity:.1f}/3)")

    ex = row.get("explain") or {}
    if "writing" in ex:
        writing = round(ex["writing"], 1)
        if writing >= 2.5:
            plus.append(f"prompt bien écrit pour un LLM : précis et détaillé ({writing:.1f}/3)")
        elif writing < 1.5:
            minus.append(f"prompt mal écrit pour un LLM : vague ou lacunaire ({writing:.1f}/3)")
    if "examples" in ex:
        if ex["examples"] >= YES:
            plus.append("donne des exemples concrets")
        elif ex["examples"] <= NO:
            minus.append("peu d'exemples concrets")
    if "steps" in ex:
        if ex["steps"] >= YES:
            plus.append("étapes claires, dans l'ordre")
        elif ex["steps"] <= NO:
            minus.append("pas de marche à suivre claire")
    if "third_party" in ex:
        if ex["third_party"] >= YES:
            minus.append("ne sert qu'avec une plateforme, un service ou un compte précis")
        elif ex["third_party"] <= NO:
            plus.append("utilisable sans service tiers")

    flags = row.get("flags", [])
    if not any(f == "éditeur en liste blanche" or f.startswith("organisation :") for f in flags):
        minus.append(f"éditeur non vérifié (confiance {row.get('score', 0):.0f}/100)")
    installs = row.get("installs", 0)
    if installs >= POPULAR:
        plus.append(f"très utilisé ({installs:,} installations)".replace(",", " "))
    elif installs < OBSCURE:
        minus.append(f"peu utilisé ({installs} installation(s))")
    for flag in flags:
        item = _from_flag(flag)
        if item:
            (plus if item[0] == "+" else minus).append(item[1])
    return plus, minus
