# skillscout v2 — Jev et routine hebdomadaire — Design

**Statut :** approuvé section par section le 2026-09-23 (approche B, paquet modulaire).
Remplace la spec du 2026-09-12 sur deux points : l'IA locale (qwen3:8b) disparaît,
et skillscout installe désormais des skills (routine hebdomadaire).

## Le problème

skillscout v0.2.0 trie bien la **provenance** et la **surface** d'un skill, mais :

1. il ne comprend pas l'**intention** du texte. Ses motifs regex ne voient ni une
   exfiltration formulée en langage courant, ni une manipulation de l'agent
   déguisée, ni un corps qui fait autre chose que ce qu'annonce la description ;
2. la pertinence est confiée à `qwen3:8b`, un petit modèle local qui se trompe sur
   les nuances et impose Ollama (5 Go, 6 Go de RAM) ;
3. il ne tourne que si l'utilisateur tape une requête. Or l'utilisateur veut que les
   meilleurs skills « méta » arrivent chez lui **sans rien faire**, chaque semaine.

## Objectifs

1. **Jev (TypeSafe) remplace qwen** : il juge la sécurité sémantique **et** la pertinence.
2. **Le tri déterministe reste le premier filtre.** Jev ne peut qu'ajouter des
   exclusions, jamais repêcher un skill écarté par les règles.
3. Une commande `skillscout routine` qui, chaque semaine, découvre, juge et
   **installe automatiquement** au plus 3 skills dans `~/.claude/skills`.
4. Tout ce qui est installé est **défaisable** en une commande et **tracé**.
5. Le code est découpé en paquet modulaire et optimisé à comportement constant.
6. Toujours **zéro dépendance** hors bibliothèque standard.

## Hors objectifs

- Mettre à jour automatiquement un skill déjà installé (v1 : le rapport signale
  seulement que le dépôt amont a bougé).
- Installer un skill contenant le moindre fichier exécutable, même d'un éditeur
  de confiance.
- Toucher aux 224 skills existants, au registre de `npx skills`
  (`~/.agents/.skill-lock.json`), ou aux skills des plugins.
- Une notification (toast, Discord). Le rapport écrit suffit en v1.
- Faire tourner la routine ailleurs que sur le PC de l'utilisateur (elle doit
  écrire dans `~/.claude/skills`).

## Décisions

| # | Décision | Motif |
|---|---|---|
| D1 | Qwen, Ollama, `--model`, `--no-llm`, `ask_qwen`, `PROMPT`, `NUM_CTX` supprimés | Demande explicite : « je veux Jev » |
| D2 | Filtre déterministe (`trust`) **puis** Jev, sur les survivants seulement | Défense en profondeur ; Jev lui-même peut être visé par une injection ; économise les appels |
| D3 | Jev est **monotone** : il ajoute des exclusions ou des ⚠, il n'en retire jamais | Un classifieur manipulable ne doit pas pouvoir réintégrer un skill |
| D4 | Sans Jev (clé absente, refusée, disjoncteur ouvert) : manuel = classement déterministe + bandeau ; routine = **aucune installation** | Fermeture en échec |
| D5 | Installation = copie des **blobs inspectés à l'empreinte `@sha`**, pas `npx skills add` | `npx skills add` prend la branche du moment, pas ce qui a été évalué |
| D6 | Skillscout tient **son propre manifeste** et n'écrit pas dans `.skill-lock.json` | Un `npx skills update` réinstallerait la branche du moment et contournerait l'inspection |
| D7 | Plafond : 3 installations par exécution, les plus pertinentes d'abord | Chaque skill alourdit le choix de Claude (224 déjà installés) |
| D8 | Déclencheur : tâche planifiée Windows, enregistrée par l'utilisateur depuis son terminal | Zéro token Claude ; indépendante de Hermes (dont les garde-fous bloquent `~/.claude`) ; l'app Claude est un conteneur MSIX |
| D9 | Un skill populaire non méta n'est installé que s'il touche la pile de l'utilisateur (`profile.toml`) | Choix de l'utilisateur |
| D10 | Découverte = requêtes thématiques (API stable) + classements `/trending` `/hot` (tolérants aux pannes) | Les classements n'ont pas d'API officielle |
| D11 | Un skill déjà jugé à la même empreinte n'est pas renvoyé à Jev | Coût et latence |
| D12 | Le slogan « zéro token facturé » disparaît du README ; il est remplacé par « aucun token Claude, les SKILL.md (publics) sont envoyés à TypeSafe » | Honnêteté : Jev est une API distante facturée |

## Architecture

```
skillscout/
  __init__.py   version
  sources.py    skills.sh : search?q= + classements /trending /hot
  github.py     gh api : dépôt, éditeur, arborescence, blobs par sha ; cache SQLite
  trust.py      règles déterministes (exécutables, motifs, fermeture en échec)
  jev.py        client TypeSafe (urllib), une relance, disjoncteur
  verdict.py    questions Jev → verdict sécurité + scores de pertinence
  rank.py       classement final
  install.py    écriture atomique des blobs, manifeste, désinstallation
  routine.py    découverte → filtres → installation plafonnée → rapport
  profile.py    lecture de profile.toml (tomllib, bibliothèque standard)
  cli.py        sous-commandes
profile.toml    profil par défaut, copié dans ~/.config/skillscout/ au premier lancement
```

Chaque module a une seule responsabilité et se teste seul. Dépendances :
`cli → routine → {sources, github, trust, verdict, rank, install}` ;
`verdict → jev` ; `github` porte le cache ; aucun module ne dépend de `cli`.

### Flux commun

```
candidats (sources)
   │
   ▼
inspection GitHub (github)  ─ en parallèle, cache
   │
   ▼
règles déterministes (trust) ──► écartés (raison + fichiers)
   │ survivants
   ▼
Jev (verdict) ── un appel par skill, toutes les questions ──► écartés / ⚠
   │
   ▼
classement (rank)
   ├─► manuel  : top 10 affiché
   └─► routine : critères d'installation (§ Routine) → install
```

## Interface

```
skillscout "besoin"            top 10 jugé par Jev
skillscout "besoin" --no-jev   classement déterministe seul (hors ligne vis-à-vis de TypeSafe)
skillscout "besoin" --json     sortie machine (scores Jev inclus s'ils existent)
skillscout "besoin" --show-excluded | --limit N    inchangés
skillscout routine             exécution de la routine (ce que lance la tâche planifiée)
skillscout routine --dry-run   tout sauf l'écriture dans ~/.claude/skills
skillscout routine --register  crée la tâche Windows hebdomadaire
skillscout routine --unregister
skillscout uninstall <nom>     retire un skill installé par skillscout
skillscout uninstall --last    retire le lot de la dernière exécution
skillscout installed           liste le manifeste
```

Clé Jev : variable d'environnement `TYPESAFE_API_KEY` (variable utilisateur Windows,
posée une fois par l'utilisateur). Jamais écrite dans un fichier par skillscout,
jamais affichée, jamais journalisée.

## Jev

### Client (`jev.py`)

Repris de `jev-guard/jev_client.py`, réécrit sur `urllib.request` :
`POST https://api.typesafe.ai/v1/systemone`, corps
`{"model": "jev-1.13.0", "state": …, "questions": …}`, réponse `answers`.
Délai 15 s (un SKILL.md est plus long qu'une commande), une relance,
disjoncteur après 3 échecs consécutifs (ouvert 5 min). 401/403 : disjoncteur
ouvert immédiatement pour toute l'exécution. Le modèle est figé (`jev-1.13.0`)
pour que les seuils calibrés restent valables.

### Ce qui est envoyé (`state`)

- `skill_md` : le texte normalisé, **le même** que celui analysé par `trust`
  (invisibles retirés, pleine chasse ramenée à l'ASCII, lignes recollées) ;
- `description` : la description déclarée dans le frontmatter ;
- `files` : la liste des chemins du dossier du skill ;
- `need` (manuel) ou `profile` (routine : thèmes méta + pile).

Le texte du skill est une **donnée** de `state`. Les instructions des questions le
désignent comme « le texte évalué », jamais comme une consigne.

### Questions

Sécurité, type Noul (probabilité de « oui »), sur le texte que Claude suivrait :

| Clé | Question |
|---|---|
| `exfiltration` | Fait-il envoyer des fichiers locaux, variables d'environnement ou identifiants vers une destination réseau ? |
| `secrets` | Fait-il lire ou afficher des fichiers d'identifiants, clés d'API, jetons, coffres de mots de passe ? |
| `destruction` | Fait-il supprimer ou écraser des données de façon irrécupérable ? |
| `download_exec` | Fait-il télécharger du code depuis Internet puis l'exécuter ? |
| `persistence` | Fait-il modifier la configuration de l'agent (`settings.json`, hooks, `CLAUDE.md`, d'autres skills) ou créer une tâche planifiée / un démarrage automatique ? |
| `manipulation` | Demande-t-il de cacher des actions à l'utilisateur, d'ignorer des consignes précédentes, ou s'adresse-t-il à celui qui l'évalue ? |
| `off_purpose` | Le corps fait-il autre chose que ce qu'annonce la description ? |

Gravité, type Score : `severity` — « Quel mal si l'agent suit ce texte à la lettre ? »
critères `Aucun`, `Mineur et réversible`, `Significatif mais récupérable`, `Grave ou irréversible` (0 à 3).

Pertinence, type Score (0 à 3) :

| Clé | Question |
|---|---|
| `meta` | À quel point améliore-t-il la façon de travailler d'un agent de code en général : méthode (planifier, tester, déboguer, revoir), maîtrise de Claude Code (sous-agents, hooks, mémoire, contexte), ou skills sur les skills ? |
| `stack` | À quel point sert-il la pile décrite dans `profile` ? (routine seulement) |
| `need` | À quel point répond-il au besoin `need` ? (manuel seulement) |
| `substance` | Est-ce une méthode réelle et exploitable, ou une coquille (texte creux, marketing, ébauche) ? |

Jev ne renvoie que des nombres. La ligne de pertinence affichée est construite par
skillscout : `besoin 3/3 · méta 2/3` en manuel, `méta 3/3 · pile 1/3` dans le
rapport de routine, suivi de la description déclarée (tronquée).
À vérifier en spike (S3) : existe-t-il un type de question qui renvoie du texte.
Si oui, c'est une amélioration ultérieure, pas une exigence de la v2.

Version 2 des questions (`QUESTIONS_VERSION = 2`, Task 14 Step 3) : après
calibration (§ Tests, point 7), `destruction`, `off_purpose` et `severity` ont
été reformulées pour distinguer les gestes de méthode de développement
ordinaires (nettoyer une branche, committer, tester, planifier) d'un vrai
danger, sans toucher aux seuils ni aux quatre autres clés de sécurité
(`exfiltration`, `secrets`, `download_exec`, `persistence`, `manipulation`),
correctement déclenchées sur les skills signalés réels de la liste (B). Le
texte anglais du code (`skillscout/verdict.py`) fait référence ; les tableaux
français ci-dessus restent des résumés.

### Seuils (`verdict.py`)

Points de départ, à calibrer (§ Tests, banc).

| | Recherche manuelle | Installation automatique |
|---|---|---|
| Écarté | un danger ≥ 0,85, **ou** ≥ 0,70 avec `severity` ≥ 2 | un danger ≥ 0,20, **ou** `severity` ≥ 2 (relevé de 1 à 2 par décision de l'utilisateur le 2026-09-23, après calibration) |
| ⚠ affiché | un danger ≥ 0,35 (nom du danger + valeur) | sans objet |
| Pertinence | tri par `need`, puis score de confiance | (`meta` ≥ 2 **ou** `stack` ≥ 2) **et** `substance` ≥ 2 |

« Danger » = l'une des 7 clés Noul de sécurité. Une clé absente de la réponse vaut
**panne pour ce skill**, pas zéro.

Ordre du classement manuel : skills écartés retirés ; puis `need` décroissant ;
puis score de confiance déterministe actuel (pénalités incluses) ; puis installations.
Sans Jev : classement v0.2.0 à l'identique.

## Routine (`routine.py`)

### Découverte

1. Une requête `search?q=<thème>&limit=100` par thème de `profile.toml`.
   Thèmes méta par défaut : `workflow`, `planning`, `tdd`, `debugging`,
   `code review`, `subagents`, `hooks`, `context`, `memory`, `prompt`,
   `skill creator`, `claude code`. Pile par défaut : `python`, `pyqt`, `rust`,
   `bevy`, `supabase`, `vercel`, `audio`, `agents`.
2. Les classements `https://www.skills.sh/trending` et `/hot` : extraction de la
   liste `initialSkills` du flux RSC embarqué dans la page. Format cassé ou page
   absente → source ignorée, mention dans le rapport, le reste continue.
3. Dédoublonnage par `(source, skillId)`.
4. Retrait des skills **déjà présents** : nom de dossier dans `~/.claude/skills`,
   entrée de `~/.agents/.skill-lock.json`, entrée du manifeste skillscout.
5. Retrait des skills **déjà jugés à la même empreinte d'arborescence** (verdict mis
   en cache, TTL 30 jours).
6. Sources non GitHub ignorées (comme en v0.2.0).

### Critères d'installation (tous requis)

1. passe `trust` (aucune exclusion déterministe) ;
2. passe le régime « installation automatique » de `verdict` ;
3. **texte pur** : le dossier du skill ne contient que `SKILL.md` et des fichiers
   `.md` / `.txt` ; aucun exécutable, lien symbolique, sous-module, binaire,
   **même chez un éditeur en liste blanche** ;
4. SKILL.md lu, frontmatter avec `name` et `description` non vides ;
5. ≥ 100 installations sur skills.sh, sauf éditeur en liste blanche ;
6. aucun dossier du même nom dans `~/.claude/skills` (on n'écrase jamais) ;
7. dans le plafond : au plus **3** par exécution, par pertinence décroissante
   (`max(meta, stack)`, puis `substance`, puis installations). Les suivants sont
   « en attente » et redeviennent candidats la semaine d'après.

### Installation (`install.py`)

1. Écrire les blobs du dossier (lus par leur sha, donc exactement ceux qui ont été
   jugés) dans `~/.claude/skills/.skillscout-tmp-<nom>/`.
2. Vérifier que le contenu écrit a les empreintes attendues.
3. Renommer d'un coup en `~/.claude/skills/<nom>/`. En cas d'échec, supprimer le
   dossier temporaire ; rien n'est laissé à moitié.
4. Ajouter au manifeste `~/.config/skillscout/installed.json` :
   `name, source, skill_id, tree_sha, installed_at, run_id, scores Jev`.

`uninstall` ne supprime **que** des dossiers présents dans le manifeste, et vérifie
que le dossier existe encore sous `~/.claude/skills/<nom>` avant de le retirer.
Un skill hors manifeste n'est jamais touché, même si son nom est donné.

### Rapport et journal

- `~/.cache/skillscout/reports/<année>-W<semaine>.md` : installés (scores et
  raison), en attente, écartés (raison), sources en panne, état de Jev, amont
  modifié pour les skills déjà installés par skillscout.
- `~/.cache/skillscout/journal.jsonl` : une ligne par exécution (run_id, dates,
  nombre de candidats, appels Jev, installés, erreurs).
- Aucun secret dans l'un ou l'autre.

### Planification

`skillscout routine --register` appelle `schtasks /Create` : hebdomadaire, lundi
10 h, compte de l'utilisateur, « exécuter dès que possible si une exécution a été
manquée ». La commande lancée est le chemin absolu de l'exécutable `skillscout`
résolu au moment de l'enregistrement. Verrou fichier
(`~/.cache/skillscout/routine.lock`) : une seconde exécution concurrente s'arrête
immédiatement.

L'utilisateur lance `--register` **depuis son propre terminal** (l'app Claude
tourne en conteneur MSIX). `gh` doit être connecté pour ce compte Windows.
Les skills installés sont pris en compte à la prochaine session Claude.

## Gestion d'erreurs

Principe : fermeture en échec. Dans le doute, on n'installe pas.

| Panne | Manuel | Routine |
|---|---|---|
| Clé absente / refusée / disjoncteur ouvert | classement déterministe + bandeau « Jev indisponible » | aucune installation ; rapport |
| Jev échoue pour un skill | skill affiché sans scores, ⚠ « non jugé par Jev » | skill non installé |
| Réponse incomplète | idem panne pour ce skill | idem |
| SKILL.md au-delà de la limite de `state` (S1) | ⚠ « non jugé par Jev » | non installé |
| skills.sh / GitHub en panne | erreur claire (comme v0.2.0) | rapport « source en panne », rien installé |
| Classement /trending ou /hot illisible | sans objet | source ignorée, rapport |
| Écriture impossible, nom pris, empreinte différente | sans objet | skill sauté, temporaire nettoyé, rapport |
| Verrou déjà pris | sans objet | sortie immédiate, journal |

## Refonte et optimisation

Étape 0 du plan, **à comportement constant** : `skillscout.py` est découpé dans le
paquet, `evaluate()` (≈ 150 lignes) est scindée par règle, la sortie est isolée.
Critère de sortie : les 186 tests existants passent, moins ceux qui ne portent que
sur qwen (supprimés avec lui, D1). Aucune fonctionnalité nouvelle avant ce jalon.
`pyproject.toml` passe de `py-modules` à `packages`, point d'entrée
`skillscout.cli:cli`. La CI reste 3.11 à 3.13 ; on ajoute un job Windows, puisque la
routine vise Windows (chemins, `schtasks`, renommage atomique).

## Tests

Tous sans réseau, sauf le banc.

1. **Refonte** : suite existante verte sur le paquet.
2. **`jev`** : faux transport : succès, 401, 403, délai dépassé, HTTP 500, JSON
   invalide, réponse tronquée, ouverture et fermeture du disjoncteur ; la clé
   n'apparaît dans aucun message d'erreur.
3. **`verdict`** : chaque seuil des deux régimes aux bornes (0,199 / 0,20 ;
   0,849 / 0,85 ; 0,70 avec gravité 1,99 / 2,0) ; clé manquante = panne ; monotonie
   (Jev ne retire jamais une exclusion de `trust`).
4. **`sources`** : pages /trending et /hot enregistrées en fixtures ; format cassé
   → liste vide + signal, pas d'exception.
5. **`routine`** : dédoublonnage, déjà installé (3 origines), déjà jugé à ce sha,
   plafond et ordre, en attente, aucune installation sans Jev, `--dry-run` n'écrit
   rien, verrou.
6. **`install`** : contenu exact, renommage atomique, nettoyage sur échec,
   conflit de nom, manifeste ; `uninstall` retire un skill du manifeste et
   **laisse intact un skill hors manifeste** portant le nom demandé.
7. **Banc de calibration Jev** (`bench/calibrate.py`, script séparé, hors CI,
   appelle la vraie API TypeSafe et GitHub) : corpus (A) skills réputés sains
   (obra/superpowers, anthropics/skills, vercel-labs/skills), pour mesurer le
   taux de refus de SÉCURITÉ à tort ; corpus (B) de vrais skills de skills.sh
   déjà signalés par le tri déterministe (motifs sensibles ou instructions
   d'exécution), listés tels quels pour relecture humaine — ils restent de
   toute façon **non installables** (`precheck` refuse tout `content_hits`),
   Jev n'ajoutant que des refus. Aucun SKILL.md piégé fabriqué (P13).

   Résultats (2026-09-23, 44 appels Jev) : (A) 13/18 refus de sécurité à tort
   (72 %), très au-dessus du seuil de 25 % retenu — dominés par `off_purpose`
   (seuil 0,20 franchi de justesse sur 10 des 18 skills, valeurs 0,20 à 0,35)
   et `destruction` (5 skills, 0,26 à 0,82, avec gravité ≥ seuil sur 5 d'entre
   eux, 1,3 à 1,8) ; `secrets` (1), `manipulation` (1) et `download_exec` (1)
   isolés ; `anthropics/skills/mcp-builder` non jugé (texte au-delà de
   `JEV_TEXT_LIMIT`). Décision de reformulation des questions laissée au
   contrôleur (Task 14, Step 3) ; seuils et questions inchangés dans cette
   tâche. (B) 4/25 skills examinés signalés par les motifs mais jugés propres
   par Jev, à relire humainement : `flowkit-labs/skills/reddit-automation`,
   `wshobson/agents/python-configuration`,
   `anthropics/claude-plugins-official/claude-automation-recommender`,
   `apollographql/skills/skill-creator`.

   **Reformulation v2** (2026-09-23, décision du contrôleur, Task 14 Step 3) :
   `destruction`, `off_purpose` et `severity` reformulées dans `verdict.py`
   (`QUESTIONS_VERSION = 2`) pour exclure les gestes de méthode de
   développement ordinaires (nettoyer une branche, committer, tester,
   planifier), sans changer les seuils ni `exfiltration` / `secrets` /
   `download_exec` / `persistence` / `manipulation`. Rejoué une fois (41
   appels Jev). Résultat (A) : 9/18 refus de sécurité à tort (50 %, contre
   72 % en v1) — encore au-dessus du seuil de 25 %, désormais porté par
   `off_purpose` (2 skills, 0,20 à 0,27, dont `doc-coauthoring` nouvellement
   refusé à 0,20 — régression mineure, au ras du seuil), `destruction`
   (2 skills, 0,22 à 0,26) et surtout `severity` (5 skills, 1,1 à 1,5,
   c.-à-d. le seuil `INSTALL_SEVERITY_MAX` lui-même, non touché par cette
   tâche) ; `secrets` (1) et `download_exec` (1) inchangés ; `mcp-builder`
   toujours non jugé (texte trop long, indépendant des questions). (B) : sur
   les 22 skills examinés dans les deux campagnes, aucun skill réellement jugé
   dangereux par Jev en v1 (au sens d'une réponse exploitée, hors panne
   réseau) n'est devenu propre en v2 — `exfiltration` / `secrets` /
   `download_exec` / `persistence` / `manipulation` continuent de signaler
   correctement les mêmes skills. La liste « à relire » passe à 7/25 (corpus
   légèrement différent : skills.sh est une source vivante, 3 candidats de v1
   ont disparu du classement, 3 nouveaux sont apparus) : les 4 de v1 restent
   propres, plus `dpearson2699/swift-ios-skills/debugging-instruments`
   (« non jugé » en v1 par panne réseau transitoire, jugé propre en v2 — pas
   une vraie régression, faute de jugement Jev comparable en v1) et deux
   skills nouvellement découverts (`alinaqi/maggy/supabase-nextjs`,
   `alinaqi/maggy/supabase`), jamais évalués en v1. Décision de seuils
   toujours laissée au contrôleur ; seuils inchangés.

   **Décision utilisateur — seuil de gravité relevé à 2** (2026-09-23, après
   calibration) : `INSTALL_SEVERITY_MAX` passe de 1,0 à 2,0 (seuils de
   danger inchangés, 0,20). Avec les questions v2 et ce seuil, recalculé sur
   les scores du run v2 sans nouvel appel Jev : (A) 7/18 refus de sécurité à
   tort (39 %), contre 9/18 (50 %) sans ce relèvement — `brainstorming`,
   `test-driven-development`, `systematic-debugging`,
   `subagent-driven-development`, `doc-coauthoring` et `find-skills` refusés
   par un danger ≥ 0,20 (indépendant du seuil de gravité) ; `mcp-builder`
   toujours non jugé (texte trop long). `executing-plans` et
   `finishing-a-development-branch`, qui n'étaient refusés que sur la
   gravité (1,3 et 1,5, sous le nouveau seuil de 2,0), passent. (B) inchangée
   à 7/25 propres : sur les skills signalés réels du run v2, tout skill dont
   la gravité atteignait 1 avait par ailleurs un danger ≥ 0,20, donc aucun
   ne devient propre avec ce seuil relevé.

## Spikes en tête de plan

| # | Question | Conséquence | Résultat (2026-09-23) |
|---|---|---|---|
| S1 | Taille maximale de `state` acceptée par Jev | Fixe la limite au-delà de laquelle un skill est « non jugé » | Sonde `bench/jev_probe.py` : 128 000 caractères passent (`status=200`, réponse complète) ; 256 000 échouent (`400 max_tokens_exceeded`). Retenu : 128 000 × 0,8 = 102 400, arrondi au millier inférieur → `verdict.JEV_TEXT_LIMIT = 102_000` |
| S2 | Latence et coût d'un appel (≈ 11 questions) | Fixe le parallélisme et confirme ≈ 100 appels/semaine acceptables | Latence médiane 0,34 s sur 5 appels séquentiels (state à 4000 caractères) → `max(15, ceil(3 × 0,34)) = 15` → `jev.TIMEOUT = 15.0` (inchangée). 4 appels en parallèle : aucun `429` → `verdict.JEV_WORKERS = 4` (inchangée) |
| S3 | Existe-t-il un type de question renvoyant du texte ? | Amélioration ultérieure de la ligne de pertinence | Types essayés `text`, `explain`, `summary` : les trois renvoient `400 api_usage_error` (« Invalid request »). Aucun type texte trouvé ; aucune constante à changer |
| S4 | Forme exacte du flux RSC `initialSkills` sur /trending et /hot | Parseur et fixtures | tranché : `initialSkills`, 600 entrées, un seul `self.__next_f.push` (P12 du plan) |

## Précisions apportées par le plan d'implémentation

- **P1** Les modules `net.py` (seul appelant de `urlopen`), `config.py` (constantes et `Paths`), `inspection.py` (`inspect_candidate`), `report.py` et `schedule.py` s'ajoutent à la liste de la spec ; `profile.toml` vit dans le paquet (`skillscout/default_profile.toml`) pour être installé avec lui.
- **P2** Point d'entrée : `skillscout.cli:cli` (enveloppe `SystemExit` autour de `main`).
- **P3** Budget de découverte : 25 candidats par requête thématique, 50 premiers de chaque classement, 300 candidats au plus après dédoublonnage (par installations décroissantes). Réglable dans `profile.toml`.
- **P4** En installation automatique, **tous** les fichiers du dossier (pas seulement `SKILL.md`) passent le scan déterministe et sont envoyés à Jev : Claude peut les lire, ils sont donc des instructions.
- **P5** Les critères peu coûteux (installations, texte pur, nom, dossier unique) sont appliqués **avant** Jev pour ne pas payer d'appels inutiles.
- **P6** Fichiers admis en « texte pur » : `*.md`, `*.txt`, et les noms `LICENSE` / `NOTICE` sans extension. Au plus 50 fichiers et 1 Mo au total.
- **P7** Ce qui est mis en cache « déjà jugé » (D11) : la **réponse Jev** par `(source, skill_id, tree_sha, version des questions)`, 30 jours. Un skill en attente n'est donc pas re-facturé la semaine suivante.
- **P8** L'intégrité à l'installation est vérifiée par l'empreinte Git de chaque blob : `sha1(b"blob <taille>\0" + octets) == sha`.
- **P9** Préparation dans `~/.claude/.skillscout-staging/` (hors du dossier des skills, même volume), puis `os.rename` vers `~/.claude/skills/<nom>`.
- **P10** `uninstall` refuse un dossier dont le contenu ne correspond plus au manifeste (modifié ou remplacé par l'utilisateur).
- **P11** La tâche est créée par PowerShell `Register-ScheduledTask` et non `schtasks /Create` : seul le premier sait poser « exécuter dès que possible si une exécution a été manquée » (`-StartWhenAvailable`). Elle lance `pythonw.exe -m skillscout routine` (pas de fenêtre) et `gh` est lancé avec `CREATE_NO_WINDOW`.
- **P12** S4 (forme des classements) est **déjà tranché** : `/`, `/trending`, `/hot` embarquent chacun, dans un unique `self.__next_f.push([1,"…"])`, une liste `"initialSkills":[…]` de 600 objets `{source, skillId, name, installs, …}`.
- **P13** Le banc de calibration (spec § Tests, point 7) n'utilise **pas** de SKILL.md piégés fabriqués. Corpus : (A) skills réputés sains, pour le taux de refus à tort ; (B) vrais skills de skills.sh déjà signalés par le tri déterministe, jugés propres par Jev ou non, listés pour relecture humaine. La garantie « aucun piège installé » repose sur la conception : un skill signalé par les motifs n'est jamais installable (`precheck`), et Jev ne fait qu'ajouter des refus.

## Décisions prises pendant l'exécution (2026-09-23)

- Le verrou de la routine est tenu par le système d'exploitation (`msvcrt`/`fcntl`) pendant toute l'exécution, plutôt que par un verrou daté : une course a été démontrée sur l'approche par âge. Il disparaît avec le processus, même tué, et le fichier `~/.cache/skillscout/routine.lock` reste sur le disque par conception (voir § Planification).
- Installation : l'entrée et la forme JSON du manifeste sont préparées avant le renommage, avec annulation sur toute exception ; la désinstallation déplace atomiquement vers la zone de préparation ; les collisions de casse entre chemins ou dossiers sont refusées (voir § Installation).
- Le rapport et le journal sont protégés et écrits sous le verrou de la routine, le journal portant `started_at`/`finished_at` (voir § Rapport et journal).
- Questions Jev en version 2 (voir § Questions, « Version 2 des questions »).
- Gravité d'installation ≥ 2 : décision utilisateur après calibration (voir § Tests, point 7, « Décision utilisateur — seuil de gravité relevé à 2 »).
- Constantes mesurées : limite de texte Jev 102 000 caractères, délai 15 s, 4 appels en parallèle (voir § Spikes en tête de plan, S1 et S2).
- Revue finale (2026-09-27), C1 : la tâche démarre dans le dossier qui contient le paquet importé (`-WorkingDirectory`), et `--register` relance d'abord l'interpréteur depuis ce dossier pour vérifier version et fichier de skillscout ; en cas d'écart (par exemple une 0.2.0 en `site-packages/skillscout.py`), il refuse et indique `py -3.14 -m pip install --upgrade .`.
- Revue finale, I4 : le README fait saisir la clé TypeSafe par `Read-Host -AsSecureString`, pour qu'elle ne reste pas dans l'historique de PowerShell.
- Revue finale, I8 : le manifeste est validé entrée par entrée (nom sûr, champs texte, chemins sûrs), les noms et chemins sont filtrés par `fullmatch`, et `uninstall` refuse toute cible dont le parent résolu n'est pas `~/.claude/skills`.
- Revue finale, I9 : les renommages d'installation et de désinstallation sont relancés jusqu'à 5 fois sur `PermissionError` (0,05 s × 2ⁿ), jamais sur `FileExistsError`.
- Revue finale, I1 : un fichier contenant des caractères invisibles ou de contrôle de direction (balises, sélecteurs de variante supplémentaires, contrôles bidi, catégories Cf, Co, Cn ; seuls U+200D, U+FE0F et un BOM en tête sont admis) n'est jamais installé.
- 2026-09-27, résidu de la revue finale : la règle couvre aussi les sélecteurs de variante VS1–VS15 (U+FE00–FE0E, une sonde y cachait un texte entier), les contrôles C0, DEL et C1 (catégorie Cc, sauf tabulation et fins de ligne), les sélecteurs mongols (U+180B–180F), les remplisseurs Hangul (U+115F, U+1160, U+3164, U+FFA0), le braille vide (U+2800), le liant graphème (U+034F) et les voyelles khmères invisibles (U+17B4–17B5). Vérifié sans faux refus sur les 1 183 fichiers `.md`/`.txt` des skills déjà installés.
- Revue finale, I2 et I3 : une entrée mal formée (source ou identifiant non textuel, `installs` illisible) est ignorée ; toute exception sur un thème, un classement, une inspection, un critère, une lecture ou un jugement ne touche que l'élément concerné ; le client Jev intercepte toute exception de transport et n'en garde que le nom, et n'accepte qu'une clé en ASCII imprimable sans espace.
- Revue finale, I5 et I6 : nouveau statut `source_error` quand aucune inspection GitHub ou aucune recherche skills.sh n'aboutit ; une clé refusée en cours d'exécution met l'exécution en `jev_unavailable` sans rien installer, même d'après le cache (D4).
- Revue finale, I7 : un seul candidat par nom de dossier, le mieux classé ; les homonymes d'autres dépôts sont écartés avant le plafond.
- Revue finale, M1 à M3 : la clé du cache Jev inclut l'empreinte du texte réellement jugé ; la simulation s'annonce « Simulation : N à installer » ; la routine refuse une arborescence tronquée même chez un éditeur en liste blanche.

## Recherche manuelle 2.1 (2026-09-29)

Constat : pour « Define measurable success criteria for your LLM application and
build evaluations to test it », skillscout renvoyait find-skills, azure-reliability,
web-design-guidelines… Deux causes : skills.sh (recherche sémantique) ne ramène sur
une phrase entière que des skills génériques, et skillscout retriait ses résultats
par installations avant de couper à 25, éliminant les skills pertinents peu installés
(eval-harness 9 900 installations contre 600 000 pour azure-validate). Décisions
prises avec l'utilisateur (séance de questions du 2026-09-29) :

- `-q REQUÊTE`, répétable : les requêtes envoyées à skills.sh. Le besoin (argument
  principal) reste ce que Jev compare au skill. Sans `-q`, la phrase est envoyée
  telle quelle. La traduction du besoin en vocabulaire du domaine est laissée à
  l'utilisateur (option « b » ; ni `claude -p` ni découpage local).
- Candidats : fusion des requêtes, chaque skill à son meilleur rang, ordre de
  pertinence de skills.sh ; les installations ne font que départager.
- Lots de 25 (`SEARCH_BATCH`) jusqu'à 10 skills montrables (`TOP_N`) ou le plafond
  `--limit` (75 par défaut, `SEARCH_CEILING`).
- Avec Jev : seuls les skills de pertinence au besoin ≥ 1,5/3 (`rank.NEED_MIN`)
  sont affichés, par pertinence décroissante ; « Seulement N skill(s) pertinent(s) »
  s'il en manque. Sans `-q` et avec moins de 3 résultats, une astuce propose `-q`.
- Sans Jev : ordre de skills.sh ; la confiance écarte et s'affiche, elle ne classe plus.
- Une ligne par nom de skill (`rank.add_deduplicated`) : la version la plus sûre
  (non écartée, meilleur score, meilleur rang) est seule inspectée par Jev et
  affichée ; les autres sources sont des `copies` (même SKILL.md) ou des `variants`
  (fork, traduction). Choix initial « même empreinte seulement », révisé par
  l'utilisateur après le banc en direct : les forks retouchés occupaient 5 à 8 des
  10 places.
- Relecture de la branche : les versions d'un groupe restent entières
  (`_members`), avec leur exclusion. Une version écartée n'est jamais comptée comme
  copie ou variante (`excluded_versions`, comptée dans « écarté(s) » et listée par
  `--show-excluded`). Quand Jev rejette la version gardée (danger ou besoin
  < 1,5), la suivante la plus sûre, non écartée et de contenu différent, est jugée à
  sa place (`rank.promote`, au plus `MAX_PROMOTIONS` = 2 fois par nom), quel que soit
  le lot où elle arrive. `group_rank` porte le meilleur rang du groupe sans écraser
  `relevance_rank`. Une panne de Jev (3 appels en échec d'affilée, `config.JEV_OUTAGE_CALLS` ; un skill trop long ou illisible ne compte pas) après un premier succès arrête la recherche
  (« Jev indisponible à partir du lot N — résultats partiels ») ; les non-jugés sont
  comptés dans l'en-tête. Le frontmatter vient du SKILL.md principal.
- « ✓ déjà installé » quand `~/.claude/skills/<nom>/SKILL.md` a l'empreinte de l'un
  des SKILL.md du skill (un dépôt comme affaan-m/ecc en contient 9 : original et
  traductions), fins de ligne CRLF ramenées à LF (`npx skills` sous Windows) ;
  « ✓ variante installée » si c'est une autre version du groupe ;
  « ≈ autre version installée » si le nom existe avec un autre contenu.
- Le SKILL.md principal (`skill_md_path`) est le moins profond, et non plus le
  premier par ordre alphabétique (`.agents/skills/x/` passait devant `skills/x/`).
- La routine n'est pas concernée : elle garde, par thème, les plus installés.

Contrôle : `tests/test_search_v3.py` rejoue des réponses de skills.sh enregistrées
le 2026-09-29 (`tests/fixtures/search_evals/`) ; `bench/search_probe.py` interroge
les vrais services sur trois besoins de référence. Au 2026-09-29, « debug » et
« plans » passent ; « evals » trouve eval-harness et eval-harness-first mais pas
le pack hamelsmu/evals-skills, que Jev juge moins pertinent (1,1 à 2,0/3) que les
eval-harness (2,9/3) : désaccord documenté, pas un défaut du pipeline.

## Retrait de la routine (2.2.0, 2026-09-29)

Décision de l'utilisateur : « je préfère rechercher manuellement ». La routine
hebdomadaire d'installation automatique est retirée, avec tout ce qui ne servait
qu'à elle : `routine.py`, `schedule.py` (tâche planifiée), `report.py`, `profile.py`
et `default_profile.toml`, `install.py` (installation, manifeste, `uninstall`,
`installed`), les classements /trending et /hot (`sources.fetch_leaderboard`), le
mode « install » des questions Jev et le cache des réponses Jev, `bench/calibrate.py`.
Les commandes `skillscout routine`, `uninstall` et `installed` répondent qu'elles
ont été retirées (code 2) au lieu d'être prises pour un besoin. Seule la détection
« déjà installé » survit, en lecture seule, dans `local.py`. Au moment du retrait,
aucune tâche planifiée n'existait et la routine n'avait rien installé (pas de
manifeste) ; les fichiers laissés par la simulation du 2026-09-27
(`~/.config/skillscout/profile.toml`, `~/.cache/skillscout/reports/`,
`journal.jsonl`, `routine.lock`) ne sont plus lus. Les sections de ce document
consacrées à la routine sont historiques.

## Top 5 expliqué (2.2.0, 2026-09-29)

Demande de l'utilisateur : une ligne vide entre chaque skill, 5 skills au plus,
avec pour chacun ses forces et faiblesses, en gardant ses notes. Choix faits :

- La recherche continue jusqu'à 10 skills pertinents (`config.TOP_N`), puis les 5
  meilleurs sont affichés (`config.DISPLAY_N`) : option « chercher 10, afficher 5 ».
- Forces et faiblesses tirées des mesures et de Jev (option « mesures + Jev ») :
  `explain.strengths_weaknesses` applique des règles fixes aux indicateurs du tri
  de confiance (libellés produits par `trust.py`), aux notes de Jev et à un second
  appel à Jev posé aux seuls skills affichés (`verdict.EXPLAIN_QUESTIONS`) :
  `examples`, `steps`, `third_party` (Noul ; oui ≥ 0,6, non ≤ 0,25) et `writing`
  (Score 0–3 : le SKILL.md est-il bien écrit comme instructions pour un LLM,
  précis, détaillé, avec déclencheur, étapes, contraintes et résultat attendu ;
  ajouté à la demande de l'utilisateur). La note d'écriture s'affiche avec les
  autres (« écriture x/3 ») mais ne change pas le classement. Les seuils portent
  sur la note arrondie au dixième, celle qui est affichée. Aucune phrase n'est
  rédigée par un modèle ; une réponse d'explication incomplète ne dit rien. Relecture : un motif relevé par le scan se dit « le texte mentionne », jamais « demande » (un texte défensif le déclenche aussi) ; « sans fichier exécutable » reste un fait sur l'arborescence ; si aucune explication ne revient, un message le dit.
- Pas d'explication Jev sans Jev, ni après une panne : les mesures seules restent.

## Choix et installation depuis le top 5 (2.3.0, 2026-09-29)

Demandé par l'utilisateur : après le top 5, choisir un ou plusieurs skills dans le
terminal, les installer, puis mettre à jour son catalogue personnel (dépôt privé
`claude-skills`, dont le `sync.py` range avec Jev, résume, régénère et pousse).
Ce n'est pas le retour de la routine : rien n'est installé sans un choix explicite.

- `install.py`. Question `Lesquels installer ? (ex. 1,3 · « tout » · Entrée pour aucun)`,
  reposée tant que la réponse n'est pas comprise ; le choix vaut confirmation (pas de
  second « êtes-vous sûr »). Posée seulement si stdin et stdout sont des terminaux,
  jamais avec `--json`.
- Installation : `npx skills add <source> --skill <id> -g -a claude-code -y`, Claude
  Code seulement (procédure du dépôt `claude-skills`). `source` et `id` viennent de
  skills.sh : filtrés (`local.is_safe_name`, `owner/repo` strict) avant d'atteindre
  npx, qui est un `.cmd` sous Windows.
- Empreinte : après installation, `local.local_status` doit rendre « same » ; sinon
  `npx skills remove` et message (le dépôt a changé depuis l'inspection). Un skill
  sans `skill_md_sha` n'est pas installé automatiquement (vérification impossible).
- Homonymes : « déjà installé » identique → rien ; autre version d'un skill que
  `npx skills` a installé (présent dans `~/.agents/.skill-lock.json`) → question
  `[o/N]`, non par défaut ; dossier hors registre (skill écrit ou cloné à la main)
  → jamais remplacé.
- Après installation : si `SKILLSCOUT_APRES_INSTALLATION` est définie, la commande est
  lancée une fois (shell, sortie directe). Son échec ne désinstalle rien. skillscout
  reste générique : il ne connaît pas `claude-skills`, seulement une commande.

## Limites connues

- Jev est un classifieur ; un SKILL.md peut chercher à le tromper. D2, D3, la
  question `manipulation` et le seuil strict de l'installation réduisent ce risque
  sans le supprimer.
- Les classements /trending et /hot ne sont pas une API : ils casseront un jour.
  Les requêtes thématiques restent la source principale.
- Installer automatiquement, c'est faire suivre à Claude des instructions que
  personne n'a relues. Le plafond, le texte pur, le rapport et `uninstall --last`
  bornent l'exposition ; le rapport hebdomadaire mérite un coup d'œil.
- La routine ne tourne que si le PC est allumé au moins une fois dans la semaine.
- Les SKILL.md des candidats sont envoyés à TypeSafe. Ils sont publics ; le besoin
  tapé en recherche manuelle l'est aussi.
