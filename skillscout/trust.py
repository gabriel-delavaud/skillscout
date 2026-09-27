"""Modèle de confiance : détection déterministe et score de classement."""
from __future__ import annotations

import datetime as _dt
import math
import re
import unicodedata

from . import config

EXEC_SUFFIXES = (".sh", ".bash", ".zsh", ".fish", ".nu", ".py", ".js", ".mjs",
                 ".cjs", ".ts", ".tsx", ".jsx", ".rb", ".pl", ".ps1", ".bat", ".cmd", ".command",
                 ".ipynb", ".go", ".rs", ".php", ".lua", ".swift", ".java",
                 ".kt", ".cs", ".exe", ".dll", ".so", ".dylib", ".jar", ".wasm")
# Fichiers exécutables ou déclencheurs d'exécution sans extension parlante.
EXEC_BASENAMES = ("makefile", "gnumakefile", "dockerfile", "justfile",
                  "rakefile", "package.json", ".envrc", "pyproject.toml",
                  "cargo.toml", "gemfile", ".mcp.json")
EXEC_DIRS = ("scripts", "hooks", "bin", ".husky")
# Réglages Claude Code : ils peuvent déclarer des hooks, c'est-à-dire des
# commandes lancées automatiquement à chaque action de l'agent.
EXEC_NESTED_FILES = ("/.claude/settings.json", "/.claude/settings.local.json")

TRUSTED_PUBLISHERS = frozenset({
    "anthropics", "vercel", "vercel-labs", "etalab-ia", "firebase",
    "google", "googleapis", "microsoft", "cloudflare", "supabase",
    "stripe", "obra", "pbakaus",
})

MIN_OWNER_AGE_DAYS = 365
MIN_PUBLIC_REPOS = 10      # dépôts d'origine (les forks ne comptent pas)
MIN_REPO_AGE_DAYS = 90     # un dépôt créé hier chez une vieille org reste suspect
MAX_STALE_DAYS = 365

EXEC_PENALTY = 20.0        # fichiers exécutables, ou instructions d'exécution
CONTENT_PENALTY = 10.0     # par motif sensible relevé dans le SKILL.md

# Motifs déterministes cherchés dans le texte du SKILL.md. Un skill est du
# texte que l'agent suit avec les droits de l'utilisateur : le markdown est
# exécutable par procuration. Deux niveaux :
#  - EXEC_PATTERNS : le texte demande d'exécuter du code téléchargé ou
#    dissimulé. Traité exactement comme un fichier exécutable (exclusion si
#    l'éditeur n'est pas de confiance, pénalité sinon).
#  - SENSITIVE_PATTERNS : secrets, destruction, exfiltration, ou tentative de
#    manipuler le modèle qui relit le skill. Drapeau + pénalité.
# Chaque détecteur reçoit le texte normalisé (`_normalize`) et renvoie une
# valeur vraie s'il trouve son motif. Tous restent linéaires en la taille du
# texte : un SKILL.md hostile ne doit pas pouvoir bloquer l'analyse.

_FETCH = re.compile(
    r"\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b", re.I)
_CURL = re.compile(r"\bcurl\b", re.I)
_WGET = re.compile(r"\bwget\b", re.I)
# Interpréteur en tête d'un maillon de pipe : `| sh`, `| /bin/bash`, `| iex`,
# éventuellement derrière un lanceur qui exécute la commande suivante avec
# ses options et arguments : `| exec sh`, `| xargs sh`, `| sudo -u root bash`,
# `| env VAR=1 python3`. Un seul lanceur, dont les arguments ne franchissent
# jamais le maillon (`|`, `;`, `&`) : aucun quantificateur imbriqué, donc pas
# de retour arrière exponentiel sur une chaîne de lanceurs hostile.
_LAUNCHER = r"(?:sudo|doas|exec|xargs|env|nohup|command|time|timeout|nice|stdbuf)"
_INTERPRETER = (r"(?:(?:ba|z|da|k|fi)?sh|python[0-9.]*|perl|ruby|node|php|pwsh"
                r"|powershell|iex|invoke-expression)")
_PIPE_TO_INTERPRETER = re.compile(
    r"\|\s*(?:" + _LAUNCHER + r"\b(?:\s+[^\s|;&]+)*?\s+)?(?:\S*/)?"
    + _INTERPRETER + r"\b", re.I)
_CURL_UPLOAD = re.compile(
    r"\s(?:-d|--data(?:-binary|-raw|-urlencode)?|-F|--form|-T|--upload-file"
    r"|--json|-X\s*POST|--request\s+POST)\b", re.I)
_WGET_UPLOAD = re.compile(r"\s--(?:post|body)-(?:data|file)\b", re.I)


def _then_on_same_line(first: re.Pattern, then: re.Pattern):
    """Détecteur « `first`, puis `then` plus loin sur la même ligne ».
    Chercher le premier `first` puis `then` dans la suite équivaut à tester
    chaque occurrence de `first`, sans le coût quadratique d'une regex
    `first[^\n]*then` sur une ligne truffée de `curl`."""
    def found(text: str) -> bool:
        # Seul `\n` termine une commande shell. `str.splitlines` couperait
        # aussi sur `\r`, `\x85`, U+2028…, que bash lit comme des caractères
        # ordinaires : ils cacheraient le pipe.
        for line in text.split("\n"):
            m = first.search(line)
            if m and then.search(line, m.end()):
                return True
        return False
    return found


def _any_of(*detectors):
    return lambda text: any(d(text) for d in detectors)


SKILL_MD_TOO_LONG = "SKILL.md trop long pour être analysé en entier"

EXEC_PATTERNS = {
    "téléchargement exécuté (curl/wget | sh)":
        _then_on_same_line(_FETCH, _PIPE_TO_INTERPRETER),
    "téléchargement exécuté (sh <(curl …))":
        re.compile(r"<\(\s*(?:curl|wget|iwr|irm)\b", re.I).search,
    "téléchargement exécuté (PowerShell iex (iwr …))":
        re.compile(r"\b(?:iex|invoke-expression)\b[\s(]*(?:iwr|irm|invoke-webrequest"
                   r"|invoke-restmethod|new-object\s+(?:system\.)?net\.webclient)\b",
                   re.I).search,
    "code inline (sh -c / python -c / eval)":
        re.compile(r"\b(?:(?:ba|z)?sh|python[0-9.]*)\s+-c\s|\beval\s+[\"'$`(]",
                   re.I).search,
    "décodage base64 exécuté":
        re.compile(r"\bbase64\s+(?:-[a-z]*d[a-z]*|--decode)\b|\batob\(", re.I).search,
}
SENSITIVE_PATTERNS = {
    "accès aux secrets (~/.ssh, .env, credentials)":
        re.compile(r"(?:^|[^\w.])\.ssh\b|\bid_(?:rsa|ed25519)\b|\.aws/credentials|"
                   r"(?:^|[\s\"'`/])\.env(?:\b|$)|\.netrc", re.I | re.M).search,
    "suppression récursive (rm -rf)":
        re.compile(r"\brm\s+(?:-{1,2}[\w-]+\s+)*?(?:-[a-z]*r[a-z]*|--recursive)\b",
                   re.I).search,
    "envoi de données vers l'extérieur (curl -d / POST)":
        _any_of(_then_on_same_line(_CURL, _CURL_UPLOAD),
                _then_on_same_line(_WGET, _WGET_UPLOAD)),
    "injection de prompt (« ignore les instructions… »)":
        re.compile(
            r"ignore\s+(?:all\s+|any\s+)?(?:(?:your|the|my)\s+)?"
            r"(?:(?:previous|prior|above|earlier)\s+)?instructions"
            r"|disregard\s+(?:all\s+)?(?:previous|prior|above)"
            r"|ignore[sz]?\s+(?:toutes\s+)?les\s+(?:instructions|consignes)"
            r"|(?:do\s+not|don.t|never)\s+(?:tell|inform|mention)\s+"
            r"(?:(?:this|it)\s+to\s+)?the\s+user"
            r"|\bne\s+(?:(?:le|la|les|lui|rien)\s+)?"
            r"(?:dis|dites|mentionne[sz]?|signale[sz]?)\s+"
            r"(?:(?:pas|jamais|rien)\s+)?(?:à|a)\s+l.utilisateur",
            re.I).search,
}

_INVISIBLES = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def find_executables(paths: list[str], exec_bits=()) -> list[str]:
    """Chemins constituant une surface d'exécution : extension à risque, nom
    de fichier déclencheur (Makefile, package.json, pyproject.toml,
    .mcp.json…), réglages Claude Code, fichiers situés sous `scripts/`,
    `hooks/`, `bin/`, `.husky/` ou `.github/workflows/`, ou marqués
    exécutables dans l'arbre Git (`exec_bits`). Insensible à la casse :
    `install.SH` et `Scripts/` comptent."""
    marked = set(exec_bits)
    hits = []
    for p in paths:
        low = p.lower()
        parts = low.split("/")
        rooted = f"/{low}"
        in_exec_dir = (any(seg in EXEC_DIRS for seg in parts[:-1])
                       or "/.github/workflows/" in rooted)
        if (low.endswith(EXEC_SUFFIXES) or parts[-1] in EXEC_BASENAMES
                or in_exec_dir or rooted.endswith(EXEC_NESTED_FILES)
                or p in marked):
            hits.append(p)
    return hits


def _normalize(body: str) -> str:
    """Forme sous laquelle le texte est analysé : compatibilité Unicode
    (lettres pleine chasse → ASCII), caractères invisibles retirés (`cu​rl`),
    continuations de ligne shell recollées (`curl … \\⏎ | sh`)."""
    text = unicodedata.normalize("NFKC", body).translate(_INVISIBLES)
    return re.sub(r"\\\r?\n", " ", text)


def scan_skill_md(body: str) -> tuple[list[str], list[str]]:
    """Motifs relevés dans le texte d'un SKILL.md : (instructions d'exécution,
    motifs sensibles). Déterministe, sans LLM. Ne prétend pas juger l'intention
    du texte : il nomme ce qu'il y trouve. Au-delà de SKILL_MD_SCAN_LIMIT
    caractères, le texte n'est pas vu en entier : c'est rapporté comme une
    instruction d'exécution, pour que la fin non lue ne serve pas de cachette."""
    execs = []
    if len(body) > config.SKILL_MD_SCAN_LIMIT:
        execs.append(SKILL_MD_TOO_LONG)
        body = body[:config.SKILL_MD_SCAN_LIMIT]
    text = _normalize(body)
    execs += [label for label, found in EXEC_PATTERNS.items() if found(text)]
    sens = [label for label, found in SENSITIVE_PATTERNS.items() if found(text)]
    return execs, sens


def locate_skill_mds(paths: list[str], skill_id: str) -> list[str]:
    """Tous les SKILL.md du skill : ceux d'un répertoire portant son nom, où
    qu'il soit dans le dépôt. Plusieurs peuvent coexister (un leurre
    `examples/a/` à côté du vrai `skills/a/`) et rien ne dit lequel sera
    installé : ils sont donc tous inspectés. Repli sur la racine quand le
    dépôt n'expose qu'un seul skill."""
    candidates = [p for p in paths if p.endswith("SKILL.md")]
    named = [p for p in candidates
             if len(p.split("/")) >= 2 and p.split("/")[-2] == skill_id]
    if named:
        return named
    return ["SKILL.md"] if candidates == ["SKILL.md"] else []


def locate_skill_md(paths: list[str], skill_id: str) -> str | None:
    """Premier SKILL.md du skill, pour l'affichage et la sortie JSON."""
    found = locate_skill_mds(paths, skill_id)
    return found[0] if found else None


def skill_paths(paths: list[str], skill_id: str) -> list[str]:
    """Restreint l'arborescence aux répertoires du skill : un `.py` ailleurs
    dans un monorepo ne concerne pas ce skill, et inversement un skill tiers
    dans le monorepo d'une organisation n'hérite pas de sa propreté. Si
    plusieurs répertoires portent son nom, tous comptent. Sans SKILL.md
    localisé, ou avec un SKILL.md à la racine, renvoie tout le dépôt (échec
    en fermeture)."""
    mds = locate_skill_mds(paths, skill_id)
    if not mds or any("/" not in md for md in mds):
        return paths
    prefixes = tuple(md.rsplit("/", 1)[0] + "/" for md in mds)
    return [p for p in paths if p.startswith(prefixes)]


def _age_days(iso: str, now: float) -> float:
    """Jours écoulés depuis un horodatage ISO. Renvoie l'infini si illisible,
    pour que l'absence de donnée échoue les seuils au lieu de les passer."""
    if not iso:
        return float("inf")
    try:
        ts = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ") \
                         .replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return float("inf")
    return (now - ts) / 86400


def _origin_repos(owner_meta: dict) -> int:
    """Dépôts d'origine si connus, sinon le compte brut (entrées anciennes)."""
    return owner_meta.get("source_repos", owner_meta.get("public_repos", 0))


def is_trusted_publisher(owner: str, owner_meta: dict, repo_meta: dict,
                         now: float) -> bool:
    """Liste blanche d'éditeurs, ou organisation passant les quatre seuils :
    âge du compte, dépôts d'origine, dépôt actif, dépôt pas né d'hier."""
    if owner.lower() in TRUSTED_PUBLISHERS:
        return True
    if owner_meta.get("type") != "Organization":
        return False
    age = _age_days(owner_meta.get("created_at", ""), now)
    repo_age = _age_days(repo_meta.get("created_at", ""), now)
    if age == float("inf") or repo_age == float("inf"):
        return False
    return (
        age >= MIN_OWNER_AGE_DAYS
        and _origin_repos(owner_meta) >= MIN_PUBLIC_REPOS
        and _age_days(repo_meta.get("pushed_at", ""), now) <= MAX_STALE_DAYS
        and repo_age >= MIN_REPO_AGE_DAYS
    )


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _shown(paths: list[str], n: int = 3) -> str:
    """Les premiers chemins en cause, pour une raison d'exclusion lisible."""
    return ", ".join(paths[:n]) + (", …" if len(paths) > n else "")


def _identity_exclusion(cand: dict, repo_meta: dict) -> str | None:
    # L'identité vient de l'API (`repo_meta`), jamais de la chaîne `source`
    # fournie par skills.sh : `gh api repos/{source}` suit silencieusement un
    # renommage/transfert, donc `source` peut pointer vers un autre dépôt que
    # celui réellement interrogé. Aucun repli sur `cand["source"]`.
    owner = repo_meta.get("owner_login") or ""
    full_name = repo_meta.get("full_name") or ""
    if not owner or not full_name:
        return (f"métadonnées GitHub incomplètes pour {cand['source']} : "
                f"l'identité de l'éditeur ne peut pas être confirmée")
    if full_name.lower() != cand["source"].lower():
        return (f"le dépôt {cand['source']} redirige vers {full_name} : son "
                f"identité ne peut pas être confirmée")
    return None


def _structure_exclusion(owner: str, trusted: bool, truncated: bool,
                         hidden: list[str]) -> str | None:
    if trusted:
        return None
    if truncated:
        return ("arborescence du dépôt tronquée par GitHub : la liste des "
                "fichiers est incomplète et ne peut pas garantir l'absence de "
                "code exécutable")
    if hidden:
        return (f"{len(hidden)} " + _plural(len(hidden), "lien symbolique ou sous-module",
                                            "liens symboliques ou sous-modules")
                + f" ({_shown(hidden)}) : leur contenu n'apparaît pas dans l'arbre et "
                f"ne peut pas être vérifié — éditeur non vérifié ({owner})")
    return None


def _execution_exclusion(owner: str, trusted: bool, execs: list[str],
                         exec_instr: list[str]) -> str | None:
    if trusted or not (execs or exec_instr):
        return None
    motifs = []
    if execs:
        motifs.append(f"{len(execs)} fichier(s) exécutable(s) ({_shown(execs)})")
    if exec_instr:
        motifs.append("SKILL.md demandant d'exécuter du code : " + ", ".join(exec_instr))
    return " ; ".join(motifs) + f" — éditeur non vérifié ({owner})"


def _unread_text_exclusion(owner: str, trusted: bool, check_text: bool,
                           body: str | None) -> str | None:
    # Le texte est ce que l'agent suivra. Ne pas l'avoir lu, ne pas savoir
    # lequel lire, ou n'y trouver que du vide, n'est pas l'avoir trouvé propre.
    if trusted or not check_text or (body or "").strip():
        return None
    return ("SKILL.md introuvable, illisible ou vide : le texte "
            "que l'agent suivrait n'a pas pu être vérifié — "
            f"éditeur non vérifié ({owner})")


def _provenance_score(cand: dict, repo_meta: dict, owner_meta: dict, owner: str,
                      trusted: bool, now: float) -> tuple[float, list[str]]:
    """Points de provenance, de popularité et de fraîcheur, avec leurs drapeaux."""
    score = 0.0
    flags: list[str] = []
    if owner.lower() in TRUSTED_PUBLISHERS:
        score += 50.0
        flags.append("éditeur en liste blanche")
    elif trusted:
        # Le simple statut « Organization » ne vaut rien : une org se crée en
        # trente secondes. Seule une org passant les seuils marque des points.
        score += 15.0
        flags.append(
            f"organisation : ≥{MIN_OWNER_AGE_DAYS} j, "
            f"≥{MIN_PUBLIC_REPOS} dépôts d'origine, dépôt actif"
        )
    if _age_days(owner_meta.get("created_at", ""), now) >= MIN_OWNER_AGE_DAYS:
        score += 10.0
    if _origin_repos(owner_meta) >= MIN_PUBLIC_REPOS:
        score += 5.0
    score += min(15.0, 5.0 * math.log10(1 + repo_meta.get("stars", 0)))
    stale = _age_days(repo_meta.get("pushed_at", ""), now)
    if stale <= 90:
        score += 10.0
    elif stale <= MAX_STALE_DAYS:
        score += 5.0
    else:
        flags.append("⚠ non maintenu depuis plus d'un an")
    score += min(10.0, 2.5 * math.log10(1 + cand.get("installs", 0)))
    return score, flags


def _risk_adjustments(execs: list[str], hidden: list[str], exec_instr: list[str],
                      sensitive: list[str], check_text: bool,
                      body: str | None) -> tuple[list[float], list[str]]:
    """Pénalités (dans l'ordre où elles s'appliquent) et drapeaux de risque."""
    penalties: list[float] = []
    flags: list[str] = []
    if execs:
        penalties.append(EXEC_PENALTY)
        flags.append(f"⚠ {len(execs)} "
                     f"{_plural(len(execs), 'fichier exécutable', 'fichiers exécutables')}")
    else:
        # « Sans fichier exécutable » est un fait mesuré sur l'arborescence,
        # pas un brevet de sûreté : le texte du SKILL.md est analysé à part.
        flags.append("sans fichier exécutable")
    if hidden:
        flags.append(f"⚠ {len(hidden)} " + _plural(
            len(hidden), "lien symbolique ou sous-module",
            "liens symboliques ou sous-modules"))
    if check_text and body is None:
        flags.append("⚠ SKILL.md non lu")
    if exec_instr:
        penalties.append(EXEC_PENALTY)
        flags.append("⚠ SKILL.md : " + ", ".join(exec_instr))
    for label in sensitive:
        penalties.append(CONTENT_PENALTY)
        flags.append(f"⚠ SKILL.md : {label}")
    return penalties, flags


def evaluate(cand: dict, repo_meta: dict, owner_meta: dict,
             paths: list[str], now: float, truncated: bool,
             body: str | None = None, exec_bits=(), opaque=(),
             check_text: bool = True) -> dict:
    """Applique l'exclusion stricte puis calcule le score de classement.
    `paths` est l'arborescence déjà restreinte au skill (voir `skill_paths`) ;
    `body` le texte du SKILL.md, ou None s'il n'a pas pu être lu ni même
    attribué au skill : chez un éditeur non vérifié, c'est une exclusion.
    `exec_bits` liste les chemins marqués exécutables dans l'arbre Git,
    `opaque` les liens symboliques et sous-modules, dont l'arbre ne donne pas
    le contenu. `check_text=False` limite l'évaluation à la structure
    (identité, troncature, fichiers, entrées opaques) : le texte n'est alors
    ni analysé ni exigé."""
    out = dict(cand, excluded=False, reason=None, score=0.0, flags=[],
               executables=[], content_hits=[])

    def excluded(reason: str) -> dict:
        out["excluded"] = True
        out["reason"] = reason
        return out

    reason = _identity_exclusion(cand, repo_meta)
    if reason:
        return excluded(reason)
    owner = repo_meta["owner_login"]
    trusted = is_trusted_publisher(owner, owner_meta, repo_meta, now)

    opaque_set = set(opaque)
    hidden = [p for p in paths if p in opaque_set]
    reason = _structure_exclusion(owner, trusted, truncated, hidden)
    if reason:
        return excluded(reason)

    execs = find_executables(paths, exec_bits)
    exec_instr, sensitive = scan_skill_md(body) if check_text and body else ([], [])
    out["executables"] = execs
    out["content_hits"] = exec_instr + sensitive
    reason = (_execution_exclusion(owner, trusted, execs, exec_instr)
              or _unread_text_exclusion(owner, trusted, check_text, body))
    if reason:
        return excluded(reason)

    score, flags = _provenance_score(cand, repo_meta, owner_meta, owner, trusted, now)
    penalties, risk_flags = _risk_adjustments(execs, hidden, exec_instr, sensitive,
                                              check_text, body)
    for p in penalties:          # même ordre de soustraction qu'avant la refonte
        score -= p
    out["score"] = round(score, 2)
    out["flags"] = flags + risk_flags
    return out
