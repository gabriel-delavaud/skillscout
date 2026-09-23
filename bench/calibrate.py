"""Banc de calibration Jev. Appelle la VRAIE API TypeSafe et GitHub.

(A) skills réputés sains : mesure le taux de refus de SÉCURITÉ à tort.
(B) vrais skills de skills.sh signalés par le tri déterministe : liste ceux
    que Jev jugerait propres en sécurité, pour relecture humaine.

Usage (PowerShell) :
  $env:TYPESAFE_API_KEY = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY','User')
  py -3.14 bench/calibrate.py"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skillscout import (config, github, inspection, install, jev, profile, routine,  # noqa: E402
                        sources, verdict)

HEALTHY = [("obra/superpowers", s) for s in (
    "brainstorming", "writing-plans", "executing-plans", "test-driven-development",
    "systematic-debugging", "verification-before-completion", "requesting-code-review",
    "receiving-code-review", "using-git-worktrees", "subagent-driven-development",
    "dispatching-parallel-agents", "finishing-a-development-branch", "writing-skills")] + [
    ("anthropics/skills", s) for s in (
        "skill-creator", "mcp-builder", "webapp-testing", "doc-coauthoring")] + [
    ("vercel-labs/skills", "find-skills")]
SUSPECTS_MAX = 25


def security_reason(j: verdict.Judgement) -> str | None:
    if j.status != "ok":
        return f"non jugé ({j.note})"
    risky = [f"{k} {j.dangers[k]:.2f}" for k in verdict.DANGERS
             if j.dangers[k] >= verdict.INSTALL_DANGER_MAX]
    if j.severity >= verdict.INSTALL_SEVERITY_MAX:
        risky.append(f"gravité {j.severity:.1f}")
    return ", ".join(risky) or None


def folder_text(row: dict, cache: github.Cache) -> str | None:
    rel = install.relative_files(row) or {}
    texts = {}
    for r, sha in rel.items():
        if install.is_text_file(r):
            texts[r] = github.fetch_blob_bytes(row["source"], sha, cache).decode("utf-8", "replace")
    if "SKILL.md" not in texts:
        return None
    order = ["SKILL.md"] + sorted(r for r in texts if r != "SKILL.md")
    return "\n\n".join(f"=== {r} ===\n{texts[r]}" for r in order)


def judge(client, row: dict, text: str, prof_text: str) -> verdict.Judgement:
    row = dict(row, tree_sha="")          # pas de cache : le banc mesure Jev à chaque fois
    return verdict.judge_one(row, client, "install", need=None, profile=prof_text,
                             cache=None, text=text)


def main() -> int:
    client = jev.JevClient.from_env()
    if client is None:
        print("TYPESAFE_API_KEY absente", file=sys.stderr)
        return 1
    prof = profile.parse_profile(profile.default_profile_text())
    cache = github.Cache(config.CACHE_PATH)
    now = time.time()

    print("## (A) Skills réputés sains\n")
    refused, judged = [], 0
    for source, sid in HEALTHY:
        try:
            row = inspection.inspect_candidate({"skill_id": sid, "name": sid, "source": source,
                                                "installs": 0, "relevance_rank": 0}, cache, now)
            text = folder_text(row, cache)
        except github.GhError as e:
            print(f"- {source}/{sid} : ignoré ({e})")
            continue
        if text is None:
            print(f"- {source}/{sid} : ignoré (SKILL.md introuvable)")
            continue
        j = judge(client, row, text, prof.jev_text())
        judged += 1
        reason = security_reason(j)
        if reason:
            refused.append(f"{source}/{sid}")
        rel = verdict.relevance_line(j, "install")
        print(f"- {source}/{sid} : sécurité {'REFUS ' + reason if reason else 'ok'} · {rel}")
    rate = len(refused) / judged if judged else 0.0
    print(f"\nRefus de sécurité à tort : {len(refused)}/{judged} ({rate:.0%})\n")

    print("## (B) Vrais skills signalés par le tri déterministe\n")
    cands, errors = routine.discover(prof)
    for e in errors:
        print(f"- source en panne : {e}")
    review, seen = [], 0
    for c in cands:
        if seen >= SUSPECTS_MAX:
            break
        if not sources.is_github_source(c["source"]):
            continue
        try:
            row = inspection.inspect_candidate(c, cache, now)
        except github.GhError:
            continue
        if not row.get("content_hits"):
            continue
        text = folder_text(row, cache) if row.get("body") else None
        if text is None:
            continue
        seen += 1
        j = judge(client, row, text, prof.jev_text())
        reason = security_reason(j)
        hits = ", ".join(row["content_hits"])
        print(f"- {c['source']}/{c['skill_id']} : motifs [{hits}] · Jev {reason or 'propre'}")
        if reason is None:
            review.append(f"{c['source']}/{c['skill_id']}")
    print(f"\nSignalés par les motifs mais jugés propres par Jev (à relire) : {len(review)}/{seen}")
    for r in review:
        print(f"  - {r}")
    print(f"\nAppels Jev : {client.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
