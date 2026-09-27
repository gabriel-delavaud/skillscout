"""Interface en ligne de commande."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from . import config, github, inspection, install, jev, rank, report, routine, schedule, sources, verdict


def format_top10(rows: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rows, 1):
        sha = (r.get("tree_sha") or "")[:7]
        where = f"{r['source']}@{sha}" if sha else r["source"]
        lines.append(
            f"{i:2}. {r['skill_id']:<34} {r['score']:>6.1f}  "
            f"{where:<40} {' · '.join(r.get('flags', []))}"
        )
        extra = r.get("relevance")
        if extra:
            desc = " ".join((r.get("description") or "").split())
            if len(desc) > 90:
                desc = desc[:89] + "…"
            lines.append(f"    {extra}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def format_excluded(rows: list[dict]) -> str:
    return "\n".join(f" - {r['skill_id']:<34} {r['source']:<32} {r['reason']}"
                     for r in rows)


def _judge_manual(evaluated: list[dict], need: str, no_jev: bool) -> tuple[bool, str]:
    """Jugement Jev de la recherche manuelle. Renvoie (Jev a servi, bandeau)."""
    if no_jev:
        return False, ""
    client = jev.JevClient.from_env()
    if client is None:
        return False, "Jev indisponible : TYPESAFE_API_KEY absente — classement déterministe seul."
    verdict.judge_rows(evaluated, client, "manual", need=need)
    judged = [r for r in evaluated if "jev" in r]
    if not judged:
        return False, ""
    if not any(r["jev"].status == "ok" for r in judged):
        note = judged[0]["jev"].note
        for r in judged:
            del r["jev"]
        return False, f"Jev indisponible ({note}) — classement déterministe seul."
    verdict.apply_manual(evaluated)
    for r in evaluated:
        if "jev" in r:
            r["relevance"] = verdict.relevance_line(r["jev"], "manual")
    return True, ""


def _main_search(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout",
        description="Trie les skills de skills.sh par confiance.",
        epilog="Autres commandes : skillscout routine | uninstall | installed "
               "(--help pour chacune).")
    ap.add_argument("besoin", help="ce que le skill doit savoir faire")
    ap.add_argument("--json", action="store_true",
                    help="sortie machine du top 10")
    ap.add_argument("--show-excluded", action="store_true",
                    help="liste aussi les candidats écartés et pourquoi")
    ap.add_argument("--limit", type=int, default=25,
                    help=f"candidats examinés, 1 à {config.MAX_LIMIT} (borne les appels GitHub)")
    ap.add_argument("--no-jev", action="store_true",
                    help="classement déterministe seul, sans envoyer les SKILL.md à Jev")
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

    used_jev, banner = _judge_manual(evaluated, args.besoin, args.no_jev)
    if banner:
        print(banner, file=sys.stderr)
    excluded_rows = [r for r in evaluated if r["excluded"]]
    top = (rank.rank_with_jev if used_jev else rank.rank)(evaluated, top=10)
    if not top:
        print(f"Les {len(evaluated)} candidat(s) examiné(s) ont tous été écartés.",
              file=sys.stderr)
        if args.show_excluded and excluded_rows:
            print(format_excluded(excluded_rows), file=sys.stderr)
        return 1

    if args.json:
        hide = {"body", "paths", "skill_files"}
        print(json.dumps([{k: (asdict(v) if k == "jev" else v)
                           for k, v in r.items() if k not in hide}
                          for r in top], ensure_ascii=False, indent=2))
        return 0

    title = "pertinence (Jev) puis confiance" if used_jev else "confiance"
    print(f"\nTOP 10 par {title} ({len(excluded_rows)} écarté(s) sur "
          f"{len(evaluated)} examiné(s), {len(ignored) + len(skipped)} ignoré(s))\n")
    print(format_top10(top))
    print("\nLe @sha après le dépôt identifie l'arborescence évaluée ; le "
          "SKILL.md analysé est celui de cet instantané.")

    if args.show_excluded and excluded_rows:
        print(f"\nÉcartés ({len(excluded_rows)}) :\n")
        print(format_excluded(excluded_rows))

    return 0


def _main_routine(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout routine",
        description="Découvre, juge (Jev) et installe au plus quelques skills ; "
                    "réglages dans ~/.config/skillscout/profile.toml.")
    ap.add_argument("--dry-run", action="store_true",
                    help="tout, sauf l'écriture dans ~/.claude/skills")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--register", action="store_true",
                   help="crée la tâche Windows hebdomadaire (lundi 10 h)")
    g.add_argument("--unregister", action="store_true", help="supprime la tâche Windows")
    args = ap.parse_args(argv)
    if args.register:
        return schedule.register()
    if args.unregister:
        return schedule.unregister()
    paths = config.default_paths()
    res = routine.run_routine(paths, client=jev.JevClient.from_env(), dry_run=args.dry_run)
    label = report.STATUS_LABELS.get(res.status, res.status)
    rest = f"{len(res.pending)} en attente, {len(res.rejected)} écarté(s)"
    if args.dry_run:                  # rien n'a été écrit : ne pas annoncer d'installation
        print(f"Simulation : {len(res.installed)} à installer, {rest} (routine {label}).")
    else:
        print(f"Routine {label} : {len(res.installed)} installé(s), {rest}.")
    report_path = paths.reports_dir / f"{report.iso_week_name(res.run_id)}.md"
    if report_path.exists():
        print(f"Rapport : {report_path}")
    return 0 if res.status == "ok" else 1


def _main_uninstall(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skillscout uninstall",
        description="Retire un skill installé par skillscout, et seulement ceux-là.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("nom", nargs="?", help="nom du skill (voir skillscout installed)")
    g.add_argument("--last", action="store_true", help="retire le lot de la dernière routine")
    args = ap.parse_args(argv)
    paths = config.default_paths()
    try:
        if not args.last:
            print(f"retiré : {install.uninstall(args.nom, paths)}")
            return 0
        removed, errors = install.uninstall_last(paths)
    except install.InstallError as e:
        print(str(e), file=sys.stderr)
        return 1
    for p in removed:
        print(f"retiré : {p}")
    for e in errors:
        print(e, file=sys.stderr)
    if not removed and not errors:
        print("Rien à retirer : skillscout n'a encore rien installé.")
    return 1 if errors else 0


def _main_installed(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="skillscout installed",
                            description="Skills installés par skillscout.").parse_args(argv)
    try:
        entries = install.installed(config.default_paths())
    except install.InstallError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not entries:
        print("Aucun skill installé par skillscout.")
        return 0
    for e in sorted(entries, key=lambda e: e["installed_at"]):
        print(f"{e['name']:<32} {e['source']}@{e['tree_sha'][:7]}  "
              f"{e['installed_at']}  lot {e['run_id']}")
    return 0


_SUBCOMMANDS = {"routine": _main_routine, "uninstall": _main_uninstall,
                "installed": _main_installed}


def main(argv: list[str]) -> int:
    """`skillscout "besoin"` (ou `skillscout search "besoin"`) cherche ;
    les autres sous-commandes sont reconnues par leur premier mot."""
    if argv and argv[0] in _SUBCOMMANDS:
        return _SUBCOMMANDS[argv[0]](argv[1:])
    if argv and argv[0] == "search":
        argv = argv[1:]
    return _main_search(argv)


def cli() -> None:
    """Point d'entrée `skillscout` installé par pip/pipx/uv."""
    raise SystemExit(main(sys.argv[1:]))
