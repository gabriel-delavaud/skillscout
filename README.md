# skillscout

> Trouver le bon skill pour Claude Code **sans installer n'importe quoi**.

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
   1.   Il cherche sur skills.sh                       → candidats dans l'ordre de
        │   (une recherche par requête -q, fusionnées)     pertinence de skills.sh
   2.   Il inspecte chaque dépôt GitHub                → qui publie ? depuis quand ?
        │   (en parallèle, résultats mis en cache)        y a-t-il du code exécutable
        │                                                 dans le dossier du skill ?
   3.   Il lit le texte du SKILL.md                    → demande-t-il d'exécuter du code
        │   (la version exacte qu'il a inspectée)         téléchargé ? de toucher aux
        │                                                 secrets ? de manipuler l'IA ?
   4.   Il écarte le risqué, regroupe les forks        → une ligne par skill
        │   d'un même skill
   5.   Jev (TypeSafe) lit chaque texte restant        → écarte ce que les motifs ne
        (un classifieur, pas un agent)                    voient pas, et ne garde que
                                                          ce qui répond à votre besoin
```

Tout se fait par lots de 25 candidats : tant que moins de 10 skills pertinents sont trouvés, skillscout examine le lot suivant, jusqu'à 75 candidats (`--limit`). Il affiche ensuite **les 5 meilleurs**, chacun avec ses forces et ses faiblesses.

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

Avant l'analyse, le texte est normalisé : caractères invisibles retirés, lettres « pleine chasse » ramenées à l'ASCII, lignes coupées par `\` recollées. Le texte est analysé en entier par les règles ; ce même texte normalisé, jusqu'à 102 000 caractères, est envoyé à Jev — au-delà, le skill n'est pas jugé par Jev.

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

Installez le paquet depuis le dossier cloné (n'importe quel Python ≥ 3.11 convient, `py -3.14` n'est qu'un exemple) :

    git clone https://github.com/gabriel-delavaud/skillscout.git
    cd skillscout
    py -3.14 -m pip install --upgrade .

`--upgrade` remplace une version plus ancienne déjà installée (par exemple la 0.2.0, qui tient dans un seul fichier `skillscout.py`). La commande `skillscout` est ensuite disponible ; `py -3.14 -m skillscout …` revient au même.

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
skillscout "ce que vous voulez faire" -q "mots du domaine" -q "autres mots"
```

| Option | Effet |
|---|---|
| *(aucune)* | classement jugé par Jev (déterministe seul si la clé manque) |
| `--no-jev` | classement déterministe seul, rien n'est envoyé à TypeSafe |
| `--show-excluded` | liste aussi les candidats écartés, avec la raison et les fichiers en cause |
| `--json` | sortie pour un programme plutôt que pour un humain, scores Jev inclus |
| `-q "requête"` | requête envoyée à skills.sh, répétable ; par défaut, la phrase du besoin elle-même |
| `--limit N` | plafond de candidats examinés, par lots de 25 (75 par défaut, 1 à 100) |

**La phrase décrit le besoin, les `-q` le cherchent.** Jev juge chaque skill par rapport à la phrase ; skills.sh, lui, trouve mieux avec des mots courts du domaine. « Définir des critères de réussite et construire des évaluations pour une appli LLM » ne ramène rien d'utile telle quelle, alors qu'avec `-q "eval harness" -q "llm judge"` les bons skills sortent en tête. Quand moins de 3 skills pertinents sont trouvés sans `-q`, skillscout le suggère.

---

## Lire le résultat

```
TOP 5 par pertinence (Jev) (4 écarté(s) sur 25 examiné(s), 0 ignoré(s))

 1. eval-harness                         50.0  affaan-m/ecc@d3b8a3e
    besoin 2.9/3 · méta 2.6/3 · substance 3.0/3 · écriture 2.8/3
    Eval-driven development (EDD) framework for AI coding sessions — define capability and re…
    + répond exactement au besoin (2.9/3)
    + prompt bien écrit pour un LLM : précis et détaillé (2.8/3)
    + donne des exemples concrets
    + déjà installé chez vous
    − éditeur non vérifié (confiance 50/100)
    − 2 version(s) retouchée(s) publiée(s) ailleurs (--json)

 2. langsmith-evaluator                  40.1  langchain-ai/langsmith-skills@bc2f989
    besoin 2.5/3 · méta 1.9/3 · substance 3.0/3 · écriture 2.9/3
    INVOKE THIS SKILL when building evaluation pipelines for LangSmith. Covers three core com…
    + organisation établie (plus d'un an, 10 dépôts ou plus, active)
    − ne sert qu'avec une plateforme, un service ou un compte précis
    − le texte demande : téléchargement exécuté (curl/wget | sh)
```

Chaque skill tient dans un bloc, séparé du suivant par une ligne vide :

1. le **nom**, le **score de confiance** (sur 100) et le **dépôt**, avec l'empreinte de l'arborescence inspectée ;
2. les **notes de Jev**, sur 3 :
   - **besoin** : à quel point le skill répond à votre phrase ;
   - **méta** : s'il améliore aussi la façon de travailler de Claude en général ;
   - **substance** : vraie méthode, ou coquille vide ;
   - **écriture** : si le texte est bien écrit *pour un LLM* (précis, détaillé, dit quand s'en servir, avec étapes, contraintes et résultat attendu) ;
3. la **description** du skill ;
4. ses **forces** (`+`) et **faiblesses** (`−`).

Avec Jev, seuls les skills qu'il juge pertinents pour votre besoin (au moins 1,5/3) sont retenus, du plus au moins pertinent ; s'il y en a moins de 5, l'en-tête l'annonce (« Seulement 3 skill(s) pertinent(s) trouvé(s) »). Sans Jev, l'ordre est celui de skills.sh : la confiance écarte, elle ne classe pas.

**Les forces et faiblesses ne sont rédigées par aucune IA.** Chaque phrase vient d'une règle fixe appliquée à ce que skillscout a mesuré : même mesure, même phrase. Elles viennent de trois sources :

| Source | Exemples |
|---|---|
| Notes de Jev | « répond exactement au besoin » (≥ 2,5/3), « ne répond qu'en partie » (< 2/3), « contenu mince » (substance < 1,5/3), « prompt bien écrit » (écriture ≥ 2,5/3) ou « mal écrit » (< 1,5/3), « dégâts possibles s'il est suivi à la lettre » (gravité ≥ 1,5/3) |
| Trois questions de plus à Jev, posées aux 5 skills affichés seulement | « donne des exemples concrets », « étapes claires, dans l'ordre », « ne sert qu'avec une plateforme, un service ou un compte précis » (oui au-delà de 0,6, non en deçà de 0,25 ; entre les deux, rien n'est dit) |
| Tri de confiance | éditeur reconnu ou non vérifié, fichiers exécutables, motifs sensibles dans le texte (« le texte demande : … »), risques relevés par Jev (« Jev y soupçonne : … »), maintenance, popularité (≥ 10 000 ou < 100 installations), déjà installé, autres versions publiées ailleurs |

Sans Jev (`--no-jev`, clé absente ou panne), seules les mesures du tri de confiance sont utilisées.

Pour installer le skill retenu, utilisez l'outil officiel :

```bash
npx skills add etalab-ia/skills@securite-developpement
```

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
- **La pertinence, c'est l'avis de Jev.** Il juge chaque skill isolément : un pack de plusieurs skills complémentaires peut être moins bien noté qu'un skill unique qui colle mot pour mot au besoin.
- **skillscout n'installe rien.** Il cherche, trie et explique ; l'installation reste votre décision, avec `npx skills add`. (La routine d'installation automatique des versions 2.0 et 2.1 a été retirée en 2.2.0.)

---

## Pour les curieux

- **Aucune dépendance** : uniquement la bibliothèque standard de Python.
- **287 tests**, sans aucun appel réseau, lancés à chaque commit sur Python 3.11 à 3.13, sous Linux et Windows : `python3 -m unittest discover -s tests`
- La conception complète et le plan d'implémentation sont dans [`docs/superpowers/`](docs/superpowers/).
- Les métadonnées GitHub sont mises en cache dans `~/.cache/skillscout/cache.db` : les dépôts 24 h, les éditeurs et arborescences 7 jours, les fichiers lus par empreinte 30 jours. Les recherches suivantes sont presque instantanées.

---

## Voir aussi

[**next-skill-navigator**](https://github.com/gabriel-delavaud/next-skill-navigator) — une fois vos skills installés, ce skill vous dit lequel lancer ensuite, et avec quel modèle d'IA.

---

## Licence

[MIT](LICENSE) — vous pouvez utiliser, modifier et redistribuer skillscout librement, en conservant la notice de licence.
