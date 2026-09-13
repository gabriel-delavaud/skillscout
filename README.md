# skillscout

> Trouver le bon skill pour Claude Code **sans installer n'importe quoi** — et sans dépenser un seul token.

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
        │                                                 y a-t-il du code exécutable ?
   3.   Il écarte le risqué et classe le reste         → top 10
        │
   4.   Il récupère la description des meilleurs
        │
   5.   Une IA qui tourne SUR VOTRE ORDINATEUR         → explique lesquels
        (qwen3:8b, via Ollama)                            correspondent à votre besoin
```

**Point important : le tri de sécurité (étapes 1 à 4) ne passe par aucune IA.** C'est du code, identique à chaque exécution, vérifiable ligne par ligne. L'IA locale n'intervient qu'à la fin, sur des candidats déjà filtrés, pour vous aider à choisir. Elle ne peut rien réintégrer de ce qui a été écarté.

---

## La règle de sécurité

Un skill est **écarté** si son dépôt contient du **code exécutable** (`.sh`, `.py`, `.js`, `.ts`, `.go`, `.rs`, `.php`… ou un dossier `scripts/`, `hooks/`, `bin/`) **et** que son éditeur n'est **pas de confiance**.

Un éditeur est de confiance s'il est :

- dans une **liste blanche** d'éditeurs connus (Anthropic, Vercel, Google, Microsoft, Cloudflare, Supabase, Stripe, Etalab…) ;
- **ou** une organisation GitHub qui remplit les trois conditions : compte de plus d'un an, au moins 10 dépôts publics, dépôt mis à jour dans l'année.

Un skill **uniquement en texte** (markdown pur) ne peut rien exécuter par lui-même : il est gardé quel que soit son auteur.

skillscout **échoue en fermeture** dans les cas douteux — il écarte plutôt que de rassurer à tort :

| Situation | Pourquoi c'est écarté |
|---|---|
| Le dépôt redirige vers un autre | il a pu être transféré à quelqu'un d'autre depuis son référencement |
| GitHub a tronqué la liste des fichiers | les fichiers manquants sont peut-être justement les scripts |
| L'identité de l'éditeur est introuvable | impossible de vérifier à qui on fait confiance |

---

## Installation, pas à pas

### 1. Vérifier les prérequis

Ouvrez un **Terminal** et tapez ces commandes une par une.

**Python 3.11 ou plus récent :**

```bash
python3 --version
```

Vous devez voir `Python 3.11` ou un numéro plus élevé. Sinon, installez Python depuis [python.org](https://www.python.org/downloads/).

**GitHub CLI (`gh`), connecté à votre compte :**

```bash
brew install gh
gh auth login
```

*Pourquoi ?* skillscout interroge GitHub pour chaque candidat. Sans compte, GitHub limite à **60 requêtes par heure** — une seule recherche suffirait à l'épuiser. Connecté, la limite passe à **5 000**. (`brew` est le gestionnaire de paquets de macOS : [brew.sh](https://brew.sh).)

### 2. Télécharger skillscout

```bash
git clone https://github.com/gabriel-delavaud/skillscout.git ~/skillscout
```

Cela crée un dossier `skillscout` dans votre dossier personnel.

### 3. Créer la commande `skillscout`

Ces lignes créent un petit raccourci pour pouvoir taper `skillscout` depuis n'importe où :

```bash
mkdir -p ~/bin
printf '#!/bin/sh\nexec python3 "$HOME/skillscout/skillscout.py" "$@"\n' > ~/bin/skillscout
chmod +x ~/bin/skillscout
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
```

Puis **fermez et rouvrez votre Terminal**. (Si vous utilisez bash plutôt que zsh, remplacez `~/.zshrc` par `~/.bashrc`.)

### 4. Vérifier que ça marche

```bash
skillscout --no-llm "tester la sécurité d'un site web"
```

Vous devez voir un classement s'afficher. Si c'est le cas, skillscout fonctionne.

### 5. *(Facultatif)* Ajouter les explications par l'IA locale

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

---

## Utilisation

```bash
skillscout "ce que vous voulez faire"
```

| Option | Effet |
|---|---|
| *(aucune)* | classement + explications par l'IA locale |
| `--no-llm` | classement seul, sans IA — rapide, et fonctionne sans Ollama |
| `--json` | sortie pour un programme plutôt que pour un humain |
| `--model MODELE` | utiliser un autre modèle Ollama que `qwen3:8b` |
| `--limit N` | examiner N candidats au lieu de 25 |

**Décrivez un besoin, pas un nom d'outil.** « optimiser des requêtes de base de données » donnera de meilleurs résultats que « postgres ».

---

## Lire le résultat

```
TOP 10 par confiance (18 écarté(s) sur 25 examiné(s))

 1. securite-anssi              84.3  etalab-ia/skills          éditeur en liste blanche · markdown pur
 2. planify-write-plan          29.3  aymericderbois/skills     markdown pur
```

Chaque ligne donne : le **nom du skill**, son **score de confiance**, le **dépôt** où il vit, et des **indicateurs** :

| Indicateur | Signification |
|---|---|
| `éditeur en liste blanche` | publié par un éditeur reconnu |
| `organisation : ≥365 j, ≥10 dépôts publics, dépôt actif` | organisation établie — ce sont des **faits mesurés**, pas une garantie |
| `markdown pur` | aucun code exécutable dans le dépôt |
| `⚠ 3 fichiers exécutables` | contient du code, mais l'éditeur est de confiance |
| `⚠ non maintenu depuis plus d'un an` | le dépôt semble abandonné |

Pour installer le skill retenu, utilisez l'outil officiel :

```bash
npx skills add etalab-ia/skills@securite-anssi
```

---

## Limites à connaître

skillscout réduit le risque, il ne le supprime pas. Soyez-en conscient :

- **Il juge la provenance, pas le contenu.** Un éditeur fiable peut publier un skill médiocre ; un inconnu, un excellent. Lisez toujours le `SKILL.md` avant d'installer — c'est du texte, ça prend deux minutes.
- **Une organisation GitHub se crée en trente secondes.** Les trois conditions rendent l'attaque plus coûteuse, pas impossible.
- **La liste blanche est maintenue à la main.**
- **L'index de skills.sh prend parfois du retard.** Un skill peut y figurer sous un ancien nom alors qu'il a été renommé. Dans l'exemple ci-dessus, `securite-anssi` s'appelle désormais `securite-developpement`.
- **Certaines sources de skills.sh ne sont pas des dépôts GitHub** (par exemple `smithery.ai`). skillscout ne peut pas les vérifier : il les ignore et l'indique.
- **`qwen3:8b` est un petit modèle.** Il compare bien quelques documents courts, mais il se trompera parfois sur les nuances.
- **Le classement favorise les skills populaires.** La pertinence de la recherche ne sert qu'à départager deux candidats à score égal. Un skill très pertinent mais peu installé peut ne pas apparaître.

---

## Pour les curieux

- **Aucune dépendance** : uniquement la bibliothèque standard de Python.
- **65 tests**, sans aucun appel réseau : `python3 -m unittest discover -s tests`
- La conception complète et le plan d'implémentation sont dans [`docs/superpowers/`](docs/superpowers/).
- Les métadonnées GitHub sont mises en cache 7 jours dans `~/.cache/skillscout/` : les recherches suivantes sont presque instantanées.

---

## Voir aussi

[**next-skill-navigator**](https://github.com/gabriel-delavaud/next-skill-navigator) — une fois vos skills installés, ce skill vous dit lequel lancer ensuite, et avec quel modèle d'IA.

---

## Licence

[MIT](LICENSE) — vous pouvez utiliser, modifier et redistribuer skillscout librement, en conservant la notice de licence.
