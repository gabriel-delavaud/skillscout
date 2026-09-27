# skillscout

> Trouver le bon skill pour Claude Code **sans installer n'importe quoi** — et, si vous le voulez, recevoir chaque semaine les meilleurs, déjà triés.

[![tests](https://github.com/gabriel-delavaud/skillscout/actions/workflows/tests.yml/badge.svg)](https://github.com/gabriel-delavaud/skillscout/actions/workflows/tests.yml)

---

## À quoi ça sert ?

Un **skill**, c'est une fiche de méthode que Claude Code suit : un simple fichier texte `SKILL.md` qui lui dit *quand* agir et *comment*. On en trouve des milliers sur [skills.sh](https://skills.sh).

Le problème : **un skill, ce sont des instructions que Claude va suivre avec vos droits.** Un skill mal intentionné peut lui faire lire vos fichiers, lancer des commandes, envoyer des données ailleurs. Et quand vous cherchez un skill, on vous montre surtout le nombre d'installations — qui ne dit rien de la sûreté. Un skill installé 7 fois, c'est simplement un skill que personne n'a vérifié.

**skillscout fait ce tri à votre place.** Vous décrivez votre besoin, il vous rend les candidats les plus dignes de confiance, et vous explique lesquels correspondent vraiment à ce que vous voulez faire.

---

## Ce qu'il fait, en 5 étapes

```
  vous tapez :  skillscout "tester la sécurité d'un site web"
        │
   1.   Il cherche sur skills.sh                       → jusqu'à 25 candidats
        │
   2.   Il inspecte chaque dépôt GitHub                → qui publie ? depuis quand ?
        │   (en parallèle, résultats mis en cache)        y a-t-il du code exécutable
        │                                                 dans le dossier du skill ?
   3.   Il lit le texte du SKILL.md                    → demande-t-il d'exécuter du code
        │   (la version exacte qu'il a inspectée)         téléchargé ? de toucher aux
        │                                                 secrets ? de manipuler l'IA ?
   4.   Il écarte le risqué et classe le reste         → top 10
        │
   5.   Jev (TypeSafe) lit chaque texte restant        → écarte ce que les motifs ne
        (un classifieur, pas un agent)                    voient pas, et classe par
                                                          pertinence pour votre besoin
```

**Point important : le premier tri (étapes 1 à 4) ne passe par aucune IA.** C'est du code, identique à chaque exécution, vérifiable ligne par ligne. Jev n'intervient qu'ensuite, sur les candidats restants : il peut en écarter d'autres, **jamais repêcher** un skill écarté. Le texte des skills lui est présenté comme une donnée à juger, jamais comme une consigne. Sans clé Jev, ou avec `--no-jev`, vous obtenez le classement déterministe seul.

---

## La règle de sécurité

Un skill est **écarté** si son éditeur n'est **pas de confiance** **et** que l'une de ces quatre choses est vraie :

- le **dossier du skill** contient du **code exécutable** : `.sh`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.php`, `Makefile`, `Dockerfile`, `package.json`, `pyproject.toml`, `.mcp.json`, `.claude/settings.json`… ou un dossier `scripts/`, `hooks/`, `bin/`, `.husky/`, `.github/workflows/`, ou tout fichier marqué exécutable dans Git, même sans extension (la casse ne compte pas : `install.SH` est vu) ;
- le **texte du `SKILL.md`** demande d'**exécuter du code téléchargé ou dissimulé** : `curl … | sh` (ou `| python3`, `| sudo -u root bash`, `| xargs sh`, `| iex`…), `bash <(curl …)`, `iex (iwr …)`, `bash -c`, `python -c`, `base64 -d`… ;
- le **`SKILL.md`** est **introuvable, illisible, vide, ou n'est qu'un lien symbolique** : le texte que Claude suivrait n'a pas pu être vérifié ;
- le dossier du skill contient un **lien symbolique** ou un **sous-module Git** : leur contenu n'apparaît pas dans l'arborescence GitHub, il ne peut donc pas être vérifié.

Un éditeur est de confiance s'il est :

- dans une **liste blanche** d'éditeurs connus (Anthropic, Vercel, Google, Microsoft, Cloudflare, Supabase, Stripe, Etalab, obra, pbakaus…) — la liste est dans `TRUSTED_PUBLISHERS`, en tête de [`skillscout/trust.py`](skillscout/trust.py) ;
- **ou** une organisation GitHub qui remplit quatre conditions : compte de plus d'un an, au moins 10 dépôts **d'origine** (les forks ne comptent pas), dépôt mis à jour dans l'année, dépôt créé depuis plus de 90 jours.

Un skill **sans fichier exécutable** publié par un inconnu est **gardé**, mais son texte est lu quand même. Un skill est aussi du texte que Claude exécutera : ce texte peut demander tout ce qu'un script ferait. C'est pour ça que skillscout le lit.

Avant l'analyse, le texte est normalisé : caractères invisibles retirés, lettres « pleine chasse » ramenées à l'ASCII, lignes coupées par `\` recollées. Le texte est analysé en entier par les règles ; ce même texte normalisé, jusqu'à 102 000 caractères, est envoyé à Jev — au-delà, le skill n'est pas jugé par Jev (affiché avec `⚠` en recherche, et jamais installé par la routine). Pour l'installation automatique, c'est plus strict : un fichier qui contient le moindre caractère invisible ou de contrôle (balises Unicode, sélecteurs de variante, contrôles de direction ou de terminal…) fait écarter le skill, car ces caractères peuvent cacher à vos yeux un texte que Claude, lui, lirait.

Le texte du `SKILL.md` est aussi fouillé pour des **motifs sensibles** qui, sans écarter le skill, le font descendre dans le classement et sont affichés : accès aux secrets (`~/.ssh`, `.env`, `credentials`), suppression récursive (`rm -rf`), envoi de données vers l'extérieur (`curl -d`, `POST`), et tentatives de manipuler l'IA (« ignore les instructions précédentes », « ne le dis pas à l'utilisateur »).

skillscout **échoue en fermeture** dans les cas douteux — il écarte plutôt que de rassurer à tort :

| Situation | Pourquoi c'est écarté |
|---|---|
| Le dépôt redirige vers un autre | il a pu être transféré à quelqu'un d'autre depuis son référencement |
| GitHub a tronqué la liste des fichiers | les fichiers manquants sont peut-être justement les scripts |
| L'identité de l'éditeur est introuvable | impossible de vérifier à qui on fait confiance |
| Le dossier du skill est introuvable | on inspecte alors **tout** le dépôt, pas moins |
| Le `SKILL.md` est introuvable, illisible, vide ou en lien symbolique | son texte n'a pas pu être vérifié ; un éditeur de confiance est gardé, avec `⚠ SKILL.md non lu` |
| Le dossier du skill contient un lien symbolique ou un sous-module | leur contenu n'est pas dans l'arborescence, comme pour une liste tronquée ; un éditeur de confiance est gardé, avec un drapeau |
| Le nombre de dépôts d'origine ne peut pas être compté | l'organisation est traitée comme si elle n'en avait aucun |
| Le `SKILL.md` dépasse 500 000 caractères | la fin non analysée pourrait cacher une instruction |
| Plusieurs dossiers portent le nom du skill | on ne sait pas lequel sera installé : ils sont tous inspectés |

### Ce que vous voyez est ce qui a été inspecté

Chaque ligne du résultat porte un `@sha` : l'identifiant de l'arborescence GitHub évaluée. Le `SKILL.md` analysé est lu **par son empreinte** dans cette même arborescence, pas sur la branche qui bouge. Les métadonnées du dépôt sont rafraîchies chaque jour ; dès qu'un push est constaté, l'arborescence est relue.

---

## Installation

### Prérequis

**Python 3.11 ou plus récent :**

```bash
python3 --version
```

**GitHub CLI (`gh`), connecté à votre compte :**

```bash
brew install gh
gh auth login
```

*Pourquoi ?* skillscout interroge GitHub pour chaque candidat. Sans compte, GitHub limite à **60 requêtes par heure** — une seule recherche suffirait à l'épuiser. Connecté, la limite passe à **5 000**. (`brew` est le gestionnaire de paquets de macOS : [brew.sh](https://brew.sh). Sous Linux, voir [cli.github.com](https://cli.github.com).)

### Option A — en une commande, avec `pipx` ou `uv`

```bash
pipx install git+https://github.com/gabriel-delavaud/skillscout.git
```

ou

```bash
uv tool install git+https://github.com/gabriel-delavaud/skillscout.git
```

La commande `skillscout` est alors disponible partout. Pour mettre à jour : `pipx upgrade skillscout` ou `uv tool upgrade skillscout`.

### Option B — à la main, sans rien installer

```bash
git clone https://github.com/gabriel-delavaud/skillscout.git ~/skillscout
mkdir -p ~/bin
printf '#!/bin/sh\nPYTHONPATH="$HOME/skillscout${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m skillscout "$@"\n' > ~/bin/skillscout
chmod +x ~/bin/skillscout
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
```

Puis **fermez et rouvrez votre Terminal**. (Si vous utilisez bash plutôt que zsh, remplacez `~/.zshrc` par `~/.bashrc`.)

### Sous Windows

Installez le paquet depuis le dossier cloné, avec le Python qui lancera la routine (n'importe quel Python ≥ 3.11 convient, `py -3.14` n'est qu'un exemple) :

    git clone https://github.com/gabriel-delavaud/skillscout.git
    cd skillscout
    py -3.14 -m pip install --upgrade .

`--upgrade` remplace une version plus ancienne déjà installée (par exemple la 0.2.0, qui tient dans un seul fichier `skillscout.py`) : sans cela, `py -3.14 -m skillscout` et la tâche planifiée pourraient lancer l'ancienne. La commande `skillscout` est ensuite disponible ; `py -3.14 -m skillscout …` revient au même.

### Vérifier que ça marche

```bash
skillscout --no-jev "tester la sécurité d'un site web"
```

Vous devez voir un classement s'afficher. Si c'est le cas, skillscout fonctionne.

### La clé Jev (TypeSafe)

Jev juge la sécurité *sémantique* du texte (ce qu'une liste de motifs ne voit pas) et sa pertinence. Il lui faut une clé, lue dans la variable d'environnement `TYPESAFE_API_KEY`. Sous Windows, posez-la une fois pour votre compte, dans PowerShell :

    $s = Read-Host 'Clé TypeSafe' -AsSecureString; [Environment]::SetEnvironmentVariable('TYPESAFE_API_KEY', [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)), 'User')

La clé est demandée à part, en saisie masquée : elle n'apparaît ni à l'écran ni dans l'historique de PowerShell, où une commande qui la contiendrait en clair resterait enregistrée. Rouvrez ensuite votre terminal. Les SKILL.md des candidats, qui sont publics, sont envoyés à TypeSafe, ainsi que le besoin que vous tapez. Aucun token Claude n'est consommé.

---

## Utilisation

```bash
skillscout "ce que vous voulez faire"
```

| Option | Effet |
|---|---|
| *(aucune)* | classement jugé par Jev (déterministe seul si la clé manque) |
| `--no-jev` | classement déterministe seul, rien n'est envoyé à TypeSafe |
| `--show-excluded` | liste aussi les candidats écartés, avec la raison et les fichiers en cause |
| `--json` | sortie pour un programme plutôt que pour un humain, scores Jev inclus |
| `--limit N` | examiner N candidats au lieu de 25 (1 à 100) |

**Décrivez un besoin, pas un nom d'outil.** « optimiser des requêtes de base de données » donnera de meilleurs résultats que « postgres ».

---

## Lire le résultat

```
TOP 10 par confiance (3 écarté(s) sur 12 examiné(s), 0 ignoré(s))

 1. securite-developpement       84.3  etalab-ia/skills@a69bf67        éditeur en liste blanche · sans fichier exécutable
    besoin 3.0/3 · méta 1.0/3 — Bonnes pratiques de sécurité pour le développement
 2. planify-write-plan           29.3  aymericderbois/skills@1392f39   sans fichier exécutable
 3. deploy-helper                12.1  qqun/skills@77a6cb7             sans fichier exécutable · ⚠ SKILL.md : accès aux secrets (~/.ssh, .env, credentials)
```

Chaque ligne donne : le **nom du skill**, son **score de confiance**, le **dépôt** et l'**empreinte de l'arborescence** inspectée, et des **indicateurs** :

| Indicateur | Signification |
|---|---|
| `éditeur en liste blanche` | publié par un éditeur reconnu |
| `organisation : ≥365 j, ≥10 dépôts d'origine, dépôt actif` | organisation établie — ce sont des **faits mesurés**, pas une garantie |
| `sans fichier exécutable` | aucun code exécutable dans le dossier du skill — un fait sur les fichiers, pas un brevet de sûreté |
| `⚠ 3 fichiers exécutables` | contient du code, mais l'éditeur est de confiance |
| `⚠ SKILL.md : …` | le texte du skill contient un motif sensible ; le skill est descendu dans le classement |
| `⚠ SKILL.md non lu` | le texte n'a pas pu être récupéré : rien n'a été vérifié dessus (n'apparaît que chez un éditeur de confiance ; un inconnu est écarté) |
| `⚠ 1 lien symbolique ou sous-module` | le dossier du skill contient une entrée dont le contenu n'a pas pu être inspecté (éditeur de confiance uniquement) |
| `⚠ non maintenu depuis plus d'un an` | le dépôt semble abandonné |
| `⚠ Jev : …` | Jev relève un risque (valeur de 0 à 1) sans atteindre le seuil d'exclusion |
| `⚠ non jugé par Jev (…)` | Jev n'a pas pu juger ce skill : il est classé après les autres |

Pour installer le skill retenu, utilisez l'outil officiel :

```bash
npx skills add etalab-ia/skills@securite-developpement
```

---

## La routine hebdomadaire

`skillscout routine` cherche seul, chaque semaine, les skills « méta » (méthode de travail, maîtrise de Claude Code, skills sur les skills) et les skills populaires qui servent **votre** pile, les juge, et **installe les meilleurs dans `~/.claude/skills`**, sans rien vous demander.

Parce que personne ne relit avant installation, les critères sont plus stricts que pour la recherche :

- tout le tri de sécurité ci-dessus, **plus** un seuil Jev bien plus bas : n'importe quel risque ≥ 0,20 écarte, ainsi qu'une gravité estimée ≥ 2 ;
- **texte pur** : `SKILL.md` et fichiers `.md`/`.txt` seulement, aucun exécutable, même chez un éditeur de confiance ; **chaque fichier** est scanné et lu par Jev ;
- au moins 100 installations sur skills.sh (sauf éditeur en liste blanche) ;
- jamais d'écrasement : un nom déjà pris est sauté ;
- **au plus 3 par semaine** ; les suivants attendent la semaine d'après ;
- les fichiers écrits sont **exactement** ceux qui ont été jugés (vérifiés par leur empreinte Git).

| Commande | Effet |
|---|---|
| `skillscout routine --dry-run` | tout, sauf l'installation : pour voir ce qu'elle ferait |
| `skillscout routine --register` | crée la tâche Windows (lundi 10 h, rattrapée au démarrage si le PC était éteint) |
| `skillscout routine --unregister` | supprime la tâche |
| `skillscout installed` | liste ce que skillscout a installé |
| `skillscout uninstall --last` | retire le dernier lot |
| `skillscout uninstall NOM` | retire un skill installé par skillscout (et seulement ceux-là) |

Pour la mettre en place sous Windows :

1. installez skillscout depuis le dossier cloné avec `py -3.14 -m pip install --upgrade .` (voir « Sous Windows » plus haut) ; cela remplace l'ancienne 0.2.0 si elle est présente ;
2. lancez `skillscout routine --dry-run` pour voir ce que la routine ferait ;
3. lancez `skillscout routine --register` depuis **votre** terminal PowerShell, pas depuis l'app Claude. Avant de créer la tâche, `--register` vérifie la version de skillscout qu'elle lancera, et refuse si ce n'est pas celle que vous venez d'installer.

Le rapport de chaque semaine est dans `~/.cache/skillscout/reports/` (par exemple `2026-W40.md`) : installés, en attente, écartés et pourquoi. Vos thèmes et votre pile se règlent dans `~/.config/skillscout/profile.toml`, créé au premier lancement. Un skill installé est pris en compte à la prochaine session de Claude.

---

## Limites à connaître

skillscout réduit le risque, il ne le supprime pas. Soyez-en conscient :

- **Il juge la provenance et la surface, pas l'intention.** Il relève des motifs précis dans le texte ; il ne comprend pas ce que le skill veut faire. Un éditeur fiable peut publier un skill médiocre ; un inconnu, un excellent. Lisez toujours le `SKILL.md` avant d'installer — c'est du texte, ça prend deux minutes.
- **Une organisation GitHub se crée en trente secondes.** Les quatre conditions rendent l'attaque plus coûteuse, pas impossible.
- **Le motif qu'on ne cherche pas n'est pas trouvé.** La liste des motifs est courte, lisible, et volontairement sans ambition sémantique.
- **`npx skills add` installe la branche du moment**, pas l'empreinte inspectée. Si le dépôt a reçu un push entre les deux, ce que vous installez peut différer de ce qui a été évalué. Comparez le `@sha` affiché avec l'état du dépôt en cas de doute.
- **La liste blanche est maintenue à la main.**
- **L'index de skills.sh prend parfois du retard.** Un skill peut y figurer sous un ancien nom alors qu'il a été renommé ; son `SKILL.md` est alors introuvable, et le skill est écarté si son éditeur n'est pas de confiance, même s'il est inoffensif.
- **Certaines sources de skills.sh ne sont pas des dépôts GitHub** (par exemple `smithery.ai`). skillscout ne peut pas les vérifier : il les ignore et l'indique.
- **Jev est un classifieur, il peut se tromper, et un texte peut chercher à le tromper.** C'est pour ça qu'il ne vient qu'après le tri déterministe et ne peut rien repêcher.
- **L'installation automatique fait suivre à Claude des instructions que personne n'a relues.** Le plafond, le texte pur et le seuil strict réduisent le risque sans l'annuler : jetez un œil au rapport hebdomadaire, et `skillscout uninstall --last` défait tout le lot.
- **Le classement favorise les skills populaires.** La pertinence de la recherche ne sert qu'à départager deux candidats à score égal. Un skill très pertinent mais peu installé peut ne pas apparaître.

---

## Pour les curieux

- **Aucune dépendance** : uniquement la bibliothèque standard de Python.
- **349 tests**, sans aucun appel réseau, lancés à chaque commit sur Python 3.11 à 3.13, sous Linux et Windows : `python3 -m unittest discover -s tests`
- La conception complète et le plan d'implémentation sont dans [`docs/superpowers/`](docs/superpowers/).
- Les métadonnées GitHub sont mises en cache dans `~/.cache/skillscout/` : les dépôts 24 h, les éditeurs et arborescences 7 jours, les fichiers lus par empreinte 30 jours. Les recherches suivantes sont presque instantanées.

---

## Voir aussi

[**next-skill-navigator**](https://github.com/gabriel-delavaud/next-skill-navigator) — une fois vos skills installés, ce skill vous dit lequel lancer ensuite, et avec quel modèle d'IA.

---

## Licence

[MIT](LICENSE) — vous pouvez utiliser, modifier et redistribuer skillscout librement, en conservant la notice de licence.
