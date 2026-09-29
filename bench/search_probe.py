"""Banc de la recherche manuelle, en direct : vraie API skills.sh, vrai
GitHub, vrai Jev. Pour chaque besoin de référence, vérifie que les skills
attendus sortent dans le top 10.

Usage (PowerShell, la clé est lue dans les variables utilisateur) :
  $env:TYPESAFE_API_KEY = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY','User')
  py -3.14 bench/search_probe.py            # tous les cas
  py -3.14 bench/search_probe.py evals      # un seul cas
Coût : jusqu'à 75 appels Jev par cas. N'imprime jamais la clé."""
from __future__ import annotations

import contextlib
import fnmatch
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import cli  # noqa: E402

# Attendus : motifs `propriétaire/dépôt/skill` (fnmatch), alternatives séparées par `|`.
# Un attendu regroupé sous une autre ligne (copie ou variante) compte comme trouvé.
# Le pack evals-skills est le premier choix de Claude pour ce besoin ; Jev le
# juge moins pertinent que les eval-harness (1,4 à 2,0 sur 3) : ce cas échoue
# tant que ce désaccord dure, et c'est voulu.
CASES = {
    "evals": {
        "need": "Define measurable success criteria for your LLM application "
                "and build evaluations to test it",
        "queries": ["eval harness", "llm evals", "error analysis", "llm judge"],
        "expected": ["affaan-m/ecc/eval-harness", "wshobson/agents/eval-harness-first",
                     "hamelsmu/evals-skills/*|ai-evals-course/evals-skills/*"],
    },
    "debug": {
        "need": "Find the root cause of a failing test before changing any code",
        "queries": ["systematic debugging", "root cause"],
        "expected": ["obra/superpowers/systematic-debugging"],
    },
    "plans": {
        "need": "Write a step-by-step implementation plan from a spec before coding",
        "queries": ["writing plans", "implementation plan"],
        "expected": ["obra/superpowers/writing-plans"],
    },
}


def run_case(name: str, case: dict) -> bool:
    argv = [case["need"], "--json"]
    for q in case["queries"]:
        argv += ["-q", q]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    rows = json.loads(out.getvalue()) if code == 0 else []
    # Pour chaque ligne : la source affichée, puis ses copies et variantes.
    keys = [[f"{src}/{r['skill_id']}" for src in
             [r["source"], *r.get("copies", []), *r.get("variants", [])]] for r in rows]
    print(f"\n== {name} : {case['need']}")
    for i, r in enumerate(rows, 1):
        need = (r.get("jev") or {}).get("relevance", {}).get("need")
        mark = {"same": " ✓", "other": " ≈"}.get(r.get("installed"), "")
        others = len(r.get("copies", [])) + len(r.get("variants", []))
        print(f"  {i:2}. {r['source'] + '/' + r['skill_id']:<60} besoin "
              f"{need if need is not None else '—'}{mark}" + (f" (+{others})" if others else ""))
    ok = True
    for exp in case["expected"]:
        pos = next((i for i, group in enumerate(keys, 1)
                    if any(fnmatch.fnmatchcase(k, alt) for k in group for alt in exp.split("|"))),
                   None)
        ok &= pos is not None
        print(f"  {'OK ' if pos else 'MANQUE'} {exp}" + (f" (#{pos})" if pos else ""))
    return ok


def main(argv: list[str]) -> int:
    names = argv or list(CASES)
    unknown = [n for n in names if n not in CASES]
    if unknown:
        print(f"cas inconnu(s) : {', '.join(unknown)} ; connus : {', '.join(CASES)}")
        return 2
    results = {n: run_case(n, CASES[n]) for n in names}
    print("\n" + " · ".join(f"{n} {'OK' if ok else 'ÉCHEC'}" for n, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
