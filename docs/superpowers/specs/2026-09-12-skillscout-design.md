# skillscout — Design

**Statut :** approuvé (option A) le 2026-09-12.

## Le problème

`npx skills find "<besoin>"` renvoie jusqu'à 100 résultats classés par un mélange
opaque de pertinence et de popularité. L'utilisateur doit ensuite, à la main :
identifier l'éditeur, juger sa crédibilité, vérifier si le dépôt contient du code
exécutable, et lire le SKILL.md. C'est long, et le nombre d'installations —
seul signal affiché — ne dit rien de la sûreté : un skill à 7 installations
signifie simplement que personne ne l'a audité.

## Objectifs

1. Une commande shell unique : `skillscout "<besoin>"`.
2. Un tri de confiance **déterministe**, sans LLM, donc gratuit et reproductible.
3. Un top 3 expliqué, produit par un LLM **local** (`qwen3:8b` via Ollama).
4. **Zéro token facturé.** Claude n'est jamais dans la boucle.

## Hors objectifs

- Analyser le *contenu* du code d'un skill pour y détecter une malveillance.
  On mesure la provenance et la surface de risque, pas l'intention du code.
- Installer le skill retenu. `skillscout` recommande ; `npx skills add` installe.
- Un mode interactif, un cache partagé, ou une interface graphique.

## Architecture

```
  skillscout "tester la sécurité d'un site web"
        │
   [1]  skills.sh/api/search?q=…            → 100 candidats {name, source, installs}
        │                                      on ne garde que les 25 plus installés
        │                                      (borne le nombre d'appels GitHub)
   [2]  gh api repos/{owner}/{repo}         → owner.type, stars, pushed_at
        gh api users/{owner}                → created_at, public_repos
        gh api …/git/trees/HEAD?recursive=1 → arborescence complète
        │                                      tout est mis en cache (SQLite, TTL 7 j)
   [3]  filtre d'exclusion + score          → TOP 10
        │
   [4]  raw.githubusercontent.com           → les 10 SKILL.md (tronqués à 3000 car.)
        │
   [5]  POST localhost:11434/api/chat       → qwen3:8b rend un TOP 3 expliqué
             modèle qwen3:8b
```

Les étapes 1 à 4 sont gratuites et déterministes. L'étape 5 tourne sur la machine.

## Modèle de confiance

### Exclusion stricte

Un candidat est **écarté** si son dépôt contient du code exécutable **et** que son
éditeur n'est pas de confiance.

- *Code exécutable* : tout chemin se terminant par `.sh .py .js .mjs .cjs .ts .rb
  .pl .ps1 .bat .command`, ou tout fichier situé sous un répertoire `scripts/`
  ou `hooks/`.
- *Éditeur de confiance* : présent dans la liste blanche, **ou** organisation
  GitHub passant les trois seuils.

### Liste blanche d'éditeurs

Volontairement une liste d'**éditeurs**, pas d'organisations : elle doit pouvoir
couvrir un particulier notoire. `obra` (Superpowers, 280 000 étoiles) est un
compte personnel ; une liste restreinte aux organisations l'aurait écarté à tort.

```
anthropics, vercel, vercel-labs, etalab-ia, firebase, google, googleapis,
microsoft, cloudflare, supabase, stripe, obra, pbakaus
```

### Seuils pour une organisation hors liste blanche

Les trois doivent être vrais :

| Seuil | Valeur | Ce qu'il écarte |
|---|---|---|
| Âge du compte | ≥ 365 jours | une organisation créée pour l'occasion |
| Dépôts publics | ≥ 10 | une coquille vide |
| Activité du dépôt | `pushed_at` ≤ 365 j | un projet abandonné |

### Score de classement

Appliqué aux candidats **non écartés**, pour ordonner le top 10 :

| Composante | Points |
|---|---|
| Éditeur en liste blanche | +50 |
| Organisation (hors liste blanche) | +15 |
| Compte ≥ 365 jours | +10 |
| ≥ 10 dépôts publics | +5 |
| Étoiles du dépôt | `+min(15, 5·log10(1+stars))` |
| Mis à jour ≤ 90 j | +10 (ou +5 si ≤ 365 j) |
| Installations | `+min(10, 2.5·log10(1+installs))` |
| Contient du code exécutable | **−20** (pénalité, l'exclusion ayant déjà filtré) |

Les échelles logarithmiques évitent qu'un dépôt à 100 000 étoiles écrase tout le
reste : la différence entre 10 et 100 étoiles compte plus que celle entre 10 000
et 100 000.

## Sources de données

| Source | Accès | Limite |
|---|---|---|
| `skills.sh/api/search?q=` | HTTP GET, JSON, anonyme | aucune connue |
| API GitHub | **`gh api`** (CLI authentifié) | 5 000 req/h — contre 60 en anonyme |
| `raw.githubusercontent.com` | HTTP GET | aucune |
| Ollama | `POST localhost:11434/api/chat` | locale |

Passer par `gh api` plutôt que par des requêtes HTTP directes est une contrainte
dure : en anonyme, une seule recherche épuiserait le quota horaire.

## Interface

```bash
skillscout "tester la sécurité d'un site web"
skillscout --no-llm "ranger des fichiers"   # top 10 brut, sans Qwen
skillscout --json "…"                        # sortie machine
```

Sortie par défaut : le top 3 de Qwen avec les différences expliquées, puis le
top 10 en liste compacte avec score et drapeaux (`⚠ 3 scripts`, `org vérifiée`).

## Contraintes globales

- **Python ≥ 3.11, bibliothèque standard uniquement.** `urllib`, `sqlite3`,
  `json`, `subprocess`, `unittest`. Aucun `pip install`.
- **Tests avec `unittest`**, pas pytest : pytest est absent de la machine et
  Python 3.14 refuse les installations système.
- **`gh` CLI requis et authentifié.** Le script échoue avec un message clair
  sinon, il ne retombe jamais sur l'API anonyme.
- **Ollama et `qwen3:8b` requis** pour l'étape 5 uniquement. Absents,
  `--no-llm` reste fonctionnel.
- **Sorties en français.**
- Aucun appel réseau dans les tests : les réponses HTTP et `gh` sont simulées.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `skillscout.py` | tout le pipeline, en fonctions pures autant que possible |
| `tests/test_skillscout.py` | tests unitaires, réseau simulé |
| `~/bin/skillscout` | lanceur de trois lignes appelant le module |
| `cache.db` | cache SQLite des métadonnées GitHub (gitignoré) |

Un seul module, conformément à l'option A. Il porte l'extension `.py` pour être
importable par `unittest` ; le nom de commande vient du lanceur dans `~/bin`.

## Limites connues

- **La provenance n'est pas le contenu.** Un éditeur de confiance peut publier un
  skill médiocre, et un inconnu un excellent. Le tri écarte le risque de
  provenance, pas la médiocrité.
- **La liste blanche est à maintenir à la main.** C'est le prix du choix
  « liste blanche + seuils » : elle ne se met pas à jour toute seule.
- **Une organisation GitHub se crée en trente secondes.** Les trois seuils
  relèvent le coût de l'attaque, ils ne l'annulent pas.
- **`qwen3:8b` est un petit modèle.** Il compare dix documents courts, ce qui est
  dans ses cordes, mais il se trompera parfois sur les nuances.
- **L'index de skills.sh est parfois périmé.** Constaté : `etalab-ia@securite-anssi`
  y figure encore alors que le skill a été renommé `securite-developpement`.
  Le script signale un candidat dont le SKILL.md est introuvable plutôt que de
  le taire.
