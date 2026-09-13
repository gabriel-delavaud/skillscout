# skillscout

> Trouver le bon skill pour Claude Code **sans installer n'importe quoi** — et sans dépenser un seul token.

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
   5.   Une IA qui tourne SUR VOTRE ORDINATEUR         → explique lesquels
        (qwen3:8b par défaut, via Ollama)                 correspondent à votre besoin
```

**Point important : le tri de sécurité (étapes 1 à 4) ne passe par aucune IA.** C'est du code, identique à chaque exécution, vérifiable ligne par ligne. L'IA locale n'intervient qu'à la fin, sur des candidats déjà filtrés, pour vous aider à choisir. Elle ne peut rien réintégrer de ce qui a été écarté, et le texte des skills lui est présenté comme une donnée à évaluer, jamais comme une consigne.

---

## La règle de sécurité

Un skill est **écarté** si son éditeur n'est **pas de confiance** **et** que l'une de ces deux choses est vraie :

- le **dossier du skill** contient du **code exécutable** : `.sh`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.php`, `Makefile`, `Dockerfile`, `package.json`… ou un dossier `scripts/`, `hooks/`, `bin/`, `.github/workflows/` (la casse ne compte pas : `install.SH` est vu) ;
- le **texte du `SKILL.md`** demande d'**exécuter du code téléchargé ou dissimulé** : `curl … | sh` (ou `| python3`, `| sudo bash`, `| iex`…), `bash <(curl …)`, `bash -c`, `python -c`, `base64 -d`…

Un éditeur est de confiance s'il est :

- dans une **liste blanche** d'éditeurs connus (Anthropic, Vercel, Google, Microsoft, Cloudflare, Supabase, Stripe, Etalab, obra, pbakaus…) — la liste est dans `TRUSTED_PUBLISHERS`, en tête de [`skillscout.py`](skillscout.py) ;
- **ou** une organisation GitHub qui remplit quatre conditions : compte de plus d'un an, au moins 10 dépôts **d'origine** (les forks ne comptent pas), dépôt mis à jour dans l'année, dépôt créé depuis plus de 90 jours.

Un skill **sans fichier exécutable** publié par un inconnu est **gardé**, mais son texte est lu quand même. Un skill est aussi du texte que Claude exécutera : ce texte peut demander tout ce qu'un script ferait. C'est pour ça que skillscout le lit.

Avant l'analyse, le texte est normalisé : caractères invisibles retirés, lettres « pleine chasse » ramenées à l'ASCII, lignes coupées par `\` recollées. Le texte est analysé en entier ; seul un extrait de 3 000 caractères est transmis à l'IA locale.

Le texte du `SKILL.md` est aussi fouillé pour des **motifs sensibles** qui, sans écarter le skill, le font descendre dans le classement et sont affichés : accès aux secrets (`~/.ssh`, `.env`, `credentials`), suppression récursive (`rm -rf`), envoi de données vers l'extérieur (`curl -d`, `POST`), et tentatives de manipuler l'IA (« ignore les instructions précédentes », « ne le dis pas à l'utilisateur »).

skillscout **échoue en fermeture** dans les cas douteux — il écarte plutôt que de rassurer à tort :

| Situation | Pourquoi c'est écarté |
|---|---|
| Le dépôt redirige vers un autre | il a pu être transféré à quelqu'un d'autre depuis son référencement |
| GitHub a tronqué la liste des fichiers | les fichiers manquants sont peut-être justement les scripts |
| L'identité de l'éditeur est introuvable | impossible de vérifier à qui on fait confiance |
| Le dossier du skill est introuvable | on inspecte alors **tout** le dépôt, pas moins |
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
printf '#!/bin/sh\nexec python3 "$HOME/skillscout/skillscout.py" "$@"\n' > ~/bin/skillscout
chmod +x ~/bin/skillscout
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
```

Puis **fermez et rouvrez votre Terminal**. (Si vous utilisez bash plutôt que zsh, remplacez `~/.zshrc` par `~/.bashrc`.)

### Vérifier que ça marche

```bash
skillscout --no-llm "tester la sécurité d'un site web"
```

Vous devez voir un classement s'afficher. Si c'est le cas, skillscout fonctionne.

### *(Facultatif)* Ajouter les explications par l'IA locale

Sans cette étape, skillscout vous donne le classement. Avec, il vous explique en plus **lequel choisir et pourquoi**.

```bash
brew install ollama
ollama serve
```

Dans un **second** Terminal :

```bash
ollama pull qwen3:8b
```

Le modèle pèse environ **5 Go** et demande à peu près **6 Go de mémoire vive** pendant qu'il travaille. Il tourne entièrement sur votre machine : rien n'est envoyé ailleurs, et c'est gratuit.

#### Un autre modèle, une autre machine

N'importe quel modèle de conversation servi par Ollama convient : skillscout lui parle par l'API standard. Sur une machine mieux dotée en mémoire, `qwen3:14b` (≈ 10 Go de RAM) ou `qwen3:32b` (≈ 20 Go) jugent plus finement. Deux façons de le dire :

```bash
skillscout --model qwen3:32b "ce que vous voulez faire"
```

ou, une fois pour toutes, dans votre `~/.zshrc` :

```bash
export SKILLSCOUT_MODEL=qwen3:32b
```

---

## Utilisation

```bash
skillscout "ce que vous voulez faire"
```

| Option | Effet |
|---|---|
| *(aucune)* | classement + explications par l'IA locale |
| `--no-llm` | classement seul, sans IA — rapide, et fonctionne sans Ollama |
| `--show-excluded` | liste aussi les candidats écartés, avec la raison et les fichiers en cause |
| `--json` | sortie pour un programme plutôt que pour un humain (**implique `--no-llm`**) |
| `--model MODELE` | utiliser un autre modèle Ollama (défaut : `qwen3:8b`, ou `$SKILLSCOUT_MODEL`) |
| `--limit N` | examiner N candidats au lieu de 25 (1 à 100) |

**Décrivez un besoin, pas un nom d'outil.** « optimiser des requêtes de base de données » donnera de meilleurs résultats que « postgres ».

---

## Lire le résultat

```
TOP 10 par confiance (3 écarté(s) sur 12 examiné(s), 0 ignoré(s))

 1. securite-developpement       84.3  etalab-ia/skills@a69bf67        éditeur en liste blanche · sans fichier exécutable
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
| `⚠ SKILL.md non lu` | le texte n'a pas pu être récupéré : rien n'a été vérifié dessus |
| `⚠ non maintenu depuis plus d'un an` | le dépôt semble abandonné |

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
- **L'index de skills.sh prend parfois du retard.** Un skill peut y figurer sous un ancien nom alors qu'il a été renommé ; son `SKILL.md` est alors introuvable et on inspecte tout le dépôt.
- **Certaines sources de skills.sh ne sont pas des dépôts GitHub** (par exemple `smithery.ai`). skillscout ne peut pas les vérifier : il les ignore et l'indique.
- **`qwen3:8b` est un petit modèle.** Il compare bien quelques documents courts, mais il se trompera parfois sur les nuances.
- **Le classement favorise les skills populaires.** La pertinence de la recherche ne sert qu'à départager deux candidats à score égal. Un skill très pertinent mais peu installé peut ne pas apparaître.

---

## Pour les curieux

- **Aucune dépendance** : uniquement la bibliothèque standard de Python.
- **151 tests**, sans aucun appel réseau, lancés à chaque commit sur Python 3.11 à 3.13 : `python3 -m unittest discover -s tests`
- La conception complète et le plan d'implémentation sont dans [`docs/superpowers/`](docs/superpowers/).
- Les métadonnées GitHub sont mises en cache dans `~/.cache/skillscout/` : les dépôts 24 h, les éditeurs et arborescences 7 jours, les fichiers lus par empreinte 30 jours. Les recherches suivantes sont presque instantanées.

---

## Voir aussi

[**next-skill-navigator**](https://github.com/gabriel-delavaud/next-skill-navigator) — une fois vos skills installés, ce skill vous dit lequel lancer ensuite, et avec quel modèle d'IA.

---

## Licence

[MIT](LICENSE) — vous pouvez utiliser, modifier et redistribuer skillscout librement, en conservant la notice de licence.
