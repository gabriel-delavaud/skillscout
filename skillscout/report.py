"""Rapport hebdomadaire (Markdown) et journal des exécutions (JSONL).
Aucun secret n'y figure : ni clé, ni en-tête, ni texte de skill."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from . import config

STATUS_LABELS = {
    "ok": "terminée",
    "locked": "abandonnée : une autre exécution est en cours",
    "jev_unavailable": "arrêtée : Jev indisponible, rien n'a été installé",
    "profile_error": "arrêtée : profile.toml invalide",
    "install_error": "arrêtée : manifeste de skillscout illisible",
    "error": "interrompue par une erreur inattendue",
}


def iso_week_name(run_id: str) -> str:
    dt = _dt.datetime.strptime(run_id, "%Y-%m-%dT%H:%M:%SZ")
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _skill_line(s: dict) -> str:
    where = s["source"] + (f"@{s['tree_sha'][:7]}" if s.get("tree_sha") else "")
    parts = [f"- **{s['skill_id']}**"]
    if s.get("relevance"):
        parts.append(s["relevance"])
    if s.get("description"):
        parts.append(" ".join(s["description"].split())[:120])
    line = " — ".join(parts) + f" ({where}, {s.get('installs', 0)} installations)"
    return line + (f"\n  → `{s['path']}`" if s.get("path") else "")


def render(result) -> str:
    out = [f"# skillscout — routine du {result.run_id}", ""]
    state = STATUS_LABELS.get(result.status, result.status)
    if result.dry_run:
        state += " (simulation : rien n'a été écrit)"
    out.append(f"**État :** {state}")
    if result.jev_status:
        out.append(f"**Jev :** {result.jev_status}")
    out += [f"Candidats examinés : {result.candidates} · appels Jev : {result.jev_calls}", ""]

    def section(title, lines):
        out.append(f"## {title}")
        out.extend(lines or ["(aucun)"])
        out.append("")
    verb = "À installer" if result.dry_run else "Installés"
    section(f"{verb} ({len(result.installed)})", [_skill_line(s) for s in result.installed])
    section(f"En attente ({len(result.pending)}) — plafond atteint, candidats la semaine prochaine",
            [_skill_line(s) for s in result.pending])
    section(f"Écartés ({len(result.rejected)})",
            [f"- {label} : {reason}" for label, reason in result.rejected])
    if result.source_errors:
        section("Sources en panne", [f"- {e}" for e in result.source_errors])
    if result.upstream_changed:
        section("Dépôts amont modifiés depuis l'installation (la version installée reste celle jugée)",
                [f"- {n}" for n in result.upstream_changed])
    if result.errors:
        section("Erreurs", [f"- {e}" for e in result.errors])
    out.append("Pour défaire le dernier lot : `skillscout uninstall --last`. "
               "Liste complète : `skillscout installed`.")
    return "\n".join(out) + "\n"


def write_report(paths: config.Paths, result) -> Path:
    paths.reports_dir.mkdir(parents=True, exist_ok=True)
    path = paths.reports_dir / f"{iso_week_name(result.run_id)}.md"
    path.write_text(render(result), encoding="utf-8")
    return path


def append_journal(paths: config.Paths, result) -> None:
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    entry = {"run_id": result.run_id, "status": result.status, "dry_run": result.dry_run,
             "candidates": result.candidates, "jev_calls": result.jev_calls,
             "installed": [s["skill_id"] for s in result.installed],
             "pending": len(result.pending), "rejected": len(result.rejected),
             "source_errors": len(result.source_errors), "errors": result.errors}
    with open(paths.journal, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
