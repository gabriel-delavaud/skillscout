"""Interface en ligne de commande."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, github, inspection, rank, sources


def format_top10(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        sha = (r.get("tree_sha") or "")[:7]
        where = f"{r['source']}@{sha}" if sha else r["source"]
        lines.append(
            f"{i:2}. {r['skill_id']:<34} {r['score']:>6.1f}  "
            f"{where:<40} {' · '.join(r.get('flags', []))}"
        )
    return "\n".join(lines)


def format_excluded(rows: list[dict]) -> str:
    return "\n".join(f" - {r['skill_id']:<34} {r['source']:<32} {r['reason']}"
                     for r in rows)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout",
        description="Trie les skills de skills.sh par confiance.")
    ap.add_argument("besoin", help="ce que le skill doit savoir faire")
    ap.add_argument("--json", action="store_true",
                    help="sortie machine du top 10")
    ap.add_argument("--show-excluded", action="store_true",
                    help="liste aussi les candidats écartés et pourquoi")
    ap.add_argument("--limit", type=int, default=25,
                    help=f"candidats examinés, 1 à {config.MAX_LIMIT} (borne les appels GitHub)")
    args = ap.parse_args(argv)
    if not 1 <= args.limit <= config.MAX_LIMIT:
        ap.error(f"--limit doit être entre 1 et {config.MAX_LIMIT}")

    os.makedirs(os.path.dirname(config.CACHE_PATH), exist_ok=True)
    cache = github.Cache(config.CACHE_PATH)
    now = time.time()

    try:
        candidates = sources.search_skills(args.besoin, limit=args.limit)
    except sources.SearchError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not candidates:
        print("Aucun candidat sur skills.sh pour cette recherche.", file=sys.stderr)
        return 1

    # Nommé `gh_candidates`, et non `github`, pour ne pas masquer le module
    # `github` importé ci-dessus dans le reste de la fonction.
    gh_candidates, skipped = [], []
    for c in candidates:
        (gh_candidates if sources.is_github_source(c["source"]) else skipped).append(c)
    for c in skipped:
        print(f"  ignoré {c['skill_id']} : source non GitHub ({c['source']})",
              file=sys.stderr)

    progress = sys.stderr.isatty()

    def worker(c):
        try:
            return inspection.inspect_candidate(c, cache, now)
        except github.GhError as e:
            return dict(c, gh_error=str(e))

    evaluated, ignored = [], []
    with ThreadPoolExecutor(max_workers=config.WORKERS) as pool:
        for i, row in enumerate(pool.map(worker, gh_candidates), 1):
            if progress:
                print(f"\r  {i}/{len(gh_candidates)} dépôts inspectés", end="",
                      file=sys.stderr, flush=True)
            (ignored if "gh_error" in row else evaluated).append(row)
    if progress:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr, flush=True)
    for r in ignored:
        print(f"  ignoré {r['source']} : {r['gh_error']}", file=sys.stderr)

    excluded_rows = [r for r in evaluated if r["excluded"]]
    top = rank.rank(evaluated, top=10)
    if not top:
        print(f"Les {len(evaluated)} candidat(s) examiné(s) ont tous été écartés.",
              file=sys.stderr)
        if args.show_excluded and excluded_rows:
            print(format_excluded(excluded_rows), file=sys.stderr)
        return 1

    if args.json:
        hide = {"body", "paths"}
        print(json.dumps([{k: v for k, v in r.items() if k not in hide}
                          for r in top], ensure_ascii=False, indent=2))
        return 0

    print(f"\nTOP 10 par confiance ({len(excluded_rows)} écarté(s) sur "
          f"{len(evaluated)} examiné(s), {len(ignored) + len(skipped)} ignoré(s))\n")
    print(format_top10(top))
    print("\nLe @sha après le dépôt identifie l'arborescence évaluée ; le "
          "SKILL.md analysé est celui de cet instantané.")

    if args.show_excluded and excluded_rows:
        print(f"\nÉcartés ({len(excluded_rows)}) :\n")
        print(format_excluded(excluded_rows))

    return 0


def cli() -> None:
    """Point d'entrée `skillscout` installé par pip/pipx/uv."""
    raise SystemExit(main(sys.argv[1:]))
