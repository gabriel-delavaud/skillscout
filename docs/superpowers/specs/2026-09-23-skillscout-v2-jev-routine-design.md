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

### Seuils (`verdict.py`)

Points de départ, à calibrer (§ Tests, banc).

| | Recherche manuelle | Installation automatique |
|---|---|---|
| Écarté | un danger ≥ 0,85, **ou** ≥ 0,70 avec `severity` ≥ 2 | un danger ≥ 0,20, **ou** `severity` ≥ 1 |
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
`skillscout.cli:main`. La CI reste 3.11 à 3.13 ; on ajoute un job Windows, puisque la
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
7. **Banc de calibration Jev** (script séparé, hors CI, appelle la vraie API) :
   ≈ 20 skills réputés sains (superpowers, anthropic, vercel…) + ≈ 15 SKILL.md
   piégés fabriqués (exfiltration en langage courant, modification de
   `settings.json`, « ne le dis pas à l'utilisateur », injection visant
   l'évaluateur, description mensongère…). Critère : **zéro piège installable** ;
   taux de faux refus sur les sains mesuré et consigné.

## Spikes en tête de plan

| # | Question | Conséquence |
|---|---|---|
| S1 | Taille maximale de `state` acceptée par Jev | Fixe la limite au-delà de laquelle un skill est « non jugé » |
| S2 | Latence et coût d'un appel (≈ 11 questions) | Fixe le parallélisme et confirme ≈ 100 appels/semaine acceptables |
| S3 | Existe-t-il un type de question renvoyant du texte ? | Amélioration ultérieure de la ligne de pertinence |
| S4 | Forme exacte du flux RSC `initialSkills` sur /trending et /hot | Parseur et fixtures |

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
