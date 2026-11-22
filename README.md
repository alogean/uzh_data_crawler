# uzh_crawler

Outil local qui part d’une URL `uzh.ch`, enregistre les pages découvertes, en tire un Markdown, puis écrit un résumé et des **tags fermés** pris uniquement dans [`taxonomy.json`](taxonomy.json). Le modèle tourne sur la machine, via [Ollama](https://ollama.com/). Le navigateur de taxonomie dans `browser/` n’est pas modifié.

La spécification du pipeline est dans [`docs/SPEC-pipeline-crawl-resume-tags.md`](docs/SPEC-pipeline-crawl-resume-tags.md).

## Installation

Python 3.11 ou plus récent.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Le repli JavaScript (`--js-fallback`) est optionnel :

```bash
pip install -e ".[js]"
playwright install chromium
```

## Ollama

Avant `run` ou `digest-only`, Ollama doit répondre sur `http://localhost:11434`.

Modèle prévu sur un Mac à 48 Go : un Qwen 3 d’environ 9B en 4 bits, par défaut `qwen3.5:9b`. Si ce tag n’est pas publié dans votre Ollama, passez un modèle Qwen 3.x équivalent avec `--model`. Le mode thinking est désactivé et le contexte est forcé à 32768 tokens.

```bash
ollama pull qwen3.5:9b
```

Deux workers LLM suffisent. Au-delà, réglez aussi le serveur :

```bash
export OLLAMA_NUM_PARALLEL=2
export OLLAMA_FLASH_ATTENTION=1
```

## Commandes

```bash
python -m uzh_crawler run \
  --seed https://www.phil.uzh.ch/ \
  --taxonomy taxonomy.json \
  --out runs/phil-001 \
  --model qwen3.5:9b \
  --ollama-url http://localhost:11434 \
  --max-pages 200 \
  --crawl-concurrency 20 \
  --llm-concurrency 2 \
  --lang fr
```

| Commande | Rôle |
|---|---|
| `run` | Crawl, puis résumé et tags |
| `crawl-only` | Remplit `raw/` et `crawl.json` sans modèle |
| `digest-only` | Traite les pages `fetched` du répertoire `--out` |
| `validate` | Vérifie `crawl.json` et les digests contre `taxonomy.json` |

Par défaut le crawl reste sur l’hôte de la seed. `--allow-subdomains` suit les autres hôtes `*.uzh.ch`. Un hôte hors `uzh.ch` est enregistré `out_of_scope` et n’est pas résumé. `--same-host` est le comportement par défaut.

Autres options utiles : `--max-depth`, `--rate-limit` (3 requêtes/seconde/hôte), `--ignore-robots`, `--js-fallback`, `--num-ctx`, `--temperature`.

Le client s’annonce comme `askUZH-TaxonomyCrawler/0.1 (+local; research)`. Il respecte `robots.txt`. Les URL refusées par `robots.txt` sont notées `failed` avec l’erreur `disallowed by robots.txt` ; elles ne font pas échouer la commande à elles seules. `HTTP_PROXY` / `HTTPS_PROXY` s’appliquent au crawl. Les appels à Ollama sur localhost ignorent le proxy.

Codes de sortie : `0` tout s’est terminé, `1` configuration invalide (seed, taxonomie, Ollama injoignable), `2` le run est fini mais au moins une page est `failed` ou `digest_error`.

## Sorties

```text
runs/<id>/
  crawl.json
  cache.sqlite
  raw/<hash>.md
  raw/<hash>.links.txt
  digests/<hash>.md
```

`crawl.json` est le manifeste (statut, en-têtes HTTP, tags). `raw/` contient le Markdown envoyé au modèle, pas le HTML. Le fichier `.links.txt` voisin sert à reprendre la frontière sans reparcourir le HTML. `digests/` contient le résumé en français (ou `--lang de|en`) avec un frontmatter YAML. `cache.sqlite` évite de relancer le modèle.

Relancer la même commande sur le même `--out` réutilise `ETag` / `Last-Modified` (réponse 304) et le hash du Markdown. Le modèle n’est rappelé que si le contenu, le `--model` ou le hash de `taxonomy.json` a changé. Un `--max-pages` trop bas lors d’une reprise revalide d’abord les pages déjà vues : montez la limite pour aller plus loin.

Les tags stockés sont des `id` de `taxonomy.json` :

| Axe | Cardinalité |
|---|---|
| `organisation` | exactement 1 |
| `publics` | 1 à N |
| `types_document` | exactement 1 |
| `offre_etudes` | 0 à N |

Une sortie du modèle qui sort de ce vocabulaire n’est pas marquée `digest_ok`.

## Tests

```bash
pytest
```

Les tests n’appellent ni le site UZH ni Ollama. Ils passent par un client HTTP simulé et un annotateur factice.
