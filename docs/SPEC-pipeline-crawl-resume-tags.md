# Spécification — Pipeline crawl UZH → résumé Markdown → tags fermés (LLM local)

Document destiné à un agent ou développeur chargé d’**implémenter** le pipeline. Il est autonome : ne pas dépendre de l’historique de chat.

**Dépôt :** `https://github.com/alogean/uzh_data_crawler`  
**Branche de travail :** `taxonomie-uzh`  
**Taxonomie live (lecture seule) :** `https://alogean.github.io/uzh_data_crawler/`  
**Fichier de taxonomie source :** [`taxonomy.json`](../taxonomy.json) à la racine du dépôt  
**Machine cible de référence :** MacBook Pro Apple M3 Max, **48 Go** de mémoire unifiée

---

## 1. Objectif

Construire un outil CLI (puis éventuellement lib) qui :

1. part d’**une URL de départ** (domaine ou sous-domaine UZH) ;
2. **crawle** le périmètre autorisé et enregistre toutes les URLs découvertes ;
3. pour chaque page (ou document) retenu, **extrait le texte** en Markdown ;
4. produit un **résumé Markdown** via un **LLM local** ;
5. assigne des **tags fermés** tirés exclusivement de `taxonomy.json` (aucune invention de terme) ;
6. persiste le tout dans des fichiers versionnables (JSON + Markdown), avec **reprise** et **cache** pour ne pas retraiter l’identique.

Le cœur métier n’est pas « un scraper générique » : c’est **taguer le web UZH avec la taxonomie askUZH** déjà définie.

---

## 2. Non-objectifs (v1)

- Ne pas remplacer le browser de taxonomie existant (`browser/`).
- Ne pas enrichir automatiquement `taxonomy.json` en v1 (pas d’ajout de nœuds pendant le crawl). Les tags pointent seulement vers des `id` déjà présents.
- Ne pas faire de RAG / index vectoriel en v1.
- Ne pas déployer de service cloud ; tout tourne en local (réseau uniquement pour fetcher les pages UZH et appeler Ollama/LM Studio en localhost).
- Ne pas crawler hors périmètre UZH (hôpitaux hors `uzh.ch`, ETH, sites personnels hors périmètre, etc.) sauf signalement explicite comme « hors périmètre ».

---

## 3. Artefacts déjà existants (à réutiliser)

| Artefact | Rôle |
|---|---|
| `taxonomy.json` | Vocabulaire fermé à 4 axes, termes trilingues, définitions, `status: seed`, `sources[]` |
| `browser/` | SPA de navigation de la taxonomie (ne pas casser) |
| `index.html` | Redirection vers `browser/` pour GitHub Pages |

### 3.1 Structure de `taxonomy.json` (contrat)

```json
{
  "schema_version": 1,
  "kind": "taxonomy",
  "axes": [
    {
      "id": "organisation" | "publics" | "types_document" | "offre_etudes",
      "labels": { "de": "...", "fr": "...", "en": "..." },
      "definition": { "de": "...", "fr": "...", "en": "..." },
      "nodes": [
        {
          "id": "string-unique-global",
          "parent": "string-id" | null,
          "labels": { "de": "...", "fr": "...", "en": "..." },
          "definition": { "de": "...", "fr": "...", "en": "..." },
          "status": "seed",
          "sources": ["https://..."]
        }
      ]
    }
  ]
}
```

**Axes et cardinalités de tagging (v1) :**

| Axe `id` | Signification | Cardinalité par URL |
|---|---|---|
| `organisation` | Qui tient / publie la page | **exactement 1** id de nœud |
| `publics` | À qui s’adresse la page | **1 à N** ids |
| `types_document` | Genre de contenu (les textes normatifs sont un **sous-type**) | **exactement 1** id |
| `offre_etudes` | Cycle / forme de programme si pertinent | **0 à N** ids (souvent 0) |

Les tags stockés sont les **`id`** des nœuds, jamais le libellé libre.

État actuel (ordre de grandeur) : organisation ~30, publics ~10, types_document ~17, offre_etudes ~9 nœuds. Le squelette est volontairement incomplet (pas d’instituts nommés).

---

## 4. Architecture cible

Découpler **collecte** et **LLM**. Le LLM est le goulot ; le crawl doit être rapide et asynchrone.

```text
[seed URL]
    │
    ▼
┌───────────────────┐     URLs + Markdown brut      ┌────────────────────┐
│  Crawler / Parser │ ─────────────────────────────► │  Store (disque)    │
│  HTTP + HTML/PDF  │                                │  raw/ + queue + db  │
└───────────────────┘                                └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │  Workers LLM local │
                                                     │  résumé + tags     │
                                                     └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │  Sorties           │
                                                     │  crawl.json        │
                                                     │  digests/*.md      │
                                                     │  cache.sqlite      │
                                                     └────────────────────┘
```

### 4.1 Choix technologiques recommandés (v1)

**Priorité : Python orchestrateur**, bindings natifs si besoin.

| Couche | Choix v1 | Alternative |
|---|---|---|
| Langage CLI | Python 3.11+ | Rust plus tard si volume / CLI autonome |
| HTTP async | `httpx` (HTTP/2) + `Semaphore` | `curl_cffi` si TLS fingerprint bloqué |
| HTML → Markdown | `htmd-py` ou `trafilatura` + conversion | `Docling` pour PDF/HTML complexes |
| PDF texte | `pymupdf4llm` | `Docling` si scan/OCR nécessaire |
| LLM local | **Ollama** API `localhost:11434` | LM Studio (OpenAI-compatible) |
| Sortie structurée | Pydantic v2 + `format=` / JSON Schema Ollama | |
| Cache | SQLite (`sqlite3`) + hash contenu | |
| Navigateur | **pas par défaut** ; Playwright seulement en fallback si extraction &lt; 200 car. | |

**Modèle LLM local recommandé (Mac 48 Go) :**

- Défaut résumé/tagging : `qwen3.5:9b` (ou équivalent Qwen 3.x ~9B disponible dans Ollama) en quantification 4-bit / MLX.
- Alternative : `gpt-oss:20b` si la taxonomie est trop ambiguë pour le 9B.
- **Désactiver le mode thinking** (`think=False` / équivalent) pour ce cas d’usage.
- Forcer `num_ctx` élevé (ex. 32768) : la valeur par défaut Ollama tronque souvent.

Ne pas charger un modèle 70B+ pour du tagging fermé : inutile et plus lent.

---

## 5. Périmètre de crawl

### 5.1 Entrée

```text
--seed https://www.phil.uzh.ch/
--max-pages 500
--same-host | --allow-subdomains
```

Règles v1 :

1. Normaliser les URLs (schéma, hôte, chemin, retirer fragment `#`, optionnellement query tracking).
2. Rester sur l’hôte de la seed, **ou** sur `*.uzh.ch` si `--allow-subdomains`.
3. Respecter `robots.txt` sauf flag explicite `--ignore-robots` (défaut : respecter).
4. Rate limit configurable (ex. 2–5 req/s / hôte).
5. Suivre les liens HTML `a[href]` ; enregistrer aussi les PDF découverts.
6. Déduplication stricte d’URL avant fetch.
7. Profondeur max optionnelle (`--max-depth`).

### 5.2 Exclusions (signaler, ne pas développer)

- Sites personnels de chercheurs hors CMS faculté (catégorie hors détail).
- Actualités / événements ponctuels : **à crawler et tagger** (type `actualite` / `evenement` existent dans la taxonomie), mais pas de logique métier spéciale.
- Intranet / auth : enregistrer l’URL comme `status: blocked_auth` sans retry infini.
- Domaines hors `uzh.ch` : `status: out_of_scope` (sauf PDF hébergé sur le même parcours si politique définie).

### 5.3 Extraction de contenu

Pour chaque URL fetchée avec succès :

1. Si PDF → Markdown via extracteur PDF.
2. Sinon HTML → Markdown (supprimer `script`, `style`, `nav`, `footer`, `header`, `aside` si l’outil le permet).
3. Si Markdown utile &lt; 200 caractères → une seule tentative Playwright (optionnelle, flag `--js-fallback`), sinon `status: empty`.
4. Ne **jamais** envoyer le HTML brut au LLM.

Stocker le Markdown brut sous :

```text
runs/<run_id>/raw/<url_hash>.md
```

---

## 6. Contrats de données de sortie

### 6.1 Manifeste de crawl — `runs/<run_id>/crawl.json`

```json
{
  "schema_version": 1,
  "kind": "crawl_run",
  "run_id": "2026-11-22T1900-phil",
  "seed_url": "https://www.phil.uzh.ch/",
  "started_at": "2026-11-22T19:00:00+01:00",
  "finished_at": null,
  "config": {
    "allow_subdomains": false,
    "max_pages": 500,
    "model": "qwen3.5:9b",
    "taxonomy_path": "taxonomy.json",
    "taxonomy_sha256": "..."
  },
  "stats": {
    "urls_discovered": 0,
    "urls_fetched_ok": 0,
    "urls_failed": 0,
    "urls_digested": 0
  },
  "pages": [
    {
      "url": "https://www.phil.uzh.ch/de/studium.html",
      "normalized_url": "https://www.phil.uzh.ch/de/studium.html",
      "discovered_from": "https://www.phil.uzh.ch/",
      "depth": 1,
      "http_status": 200,
      "content_type": "text/html",
      "fetched_at": "2026-11-22T19:01:02+01:00",
      "raw_path": "raw/ab12cd34ef56.md",
      "content_hash": "blake3-or-sha256-of-normalized-markdown",
      "etag": null,
      "last_modified": null,
      "status": "fetched" | "digest_ok" | "digest_error" | "empty" | "blocked_auth" | "out_of_scope" | "failed",
      "title": "Studium",
      "digest_path": "digests/ab12cd34ef56.md",
      "tags": {
        "organisation": "philosophische",
        "publics": ["studierende", "studieninteressierte"],
        "types_document": "portail",
        "offre_etudes": ["bachelor", "master"]
      },
      "error": null
    }
  ]
}
```

Contraintes :

- `tags.organisation` : string id ∈ axe organisation.
- `tags.publics` : liste non vide d’ids ∈ axe publics.
- `tags.types_document` : string id ∈ axe types_document.
- `tags.offre_etudes` : liste (peut être `[]`).
- Tout id inconnu → **échec de validation** (ne pas écrire `digest_ok`).

### 6.2 Digest Markdown — `runs/<run_id>/digests/<hash>.md`

Frontmatter YAML + corps résumé :

```markdown
---
title: "…"
source: "https://…"
content_hash: "…"
model: "qwen3.5:9b"
taxonomy_sha256: "…"
tags:
  organisation: philosophische
  publics:
    - studierende
  types_document: portail
  offre_etudes: []
---

## Résumé

…
```

Le résumé est en **français** par défaut (configurable `--lang fr|de|en`).

### 6.3 Cache SQLite — `runs/<run_id>/cache.sqlite` (ou `data/cache.sqlite` partagé)

Table minimale :

```sql
CREATE TABLE documents (
  source TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  etag TEXT,
  last_modified TEXT,
  output_file TEXT NOT NULL,
  model TEXT NOT NULL,
  taxonomy_sha256 TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
```

Double barrière :

1. **HTTP** : `If-None-Match` / `If-Modified-Since` → 304 = skip fetch+LLM.
2. **Contenu** : hash du Markdown normalisé identique + fichier digest présent + même `model` + même `taxonomy_sha256` → skip LLM.

Si le modèle ou le hash de taxonomie change → retraiter.

---

## 7. Tagging fermé via LLM (cœur)

### 7.1 Principe

1. Charger `taxonomy.json`.
2. Construire dynamiquement un schéma JSON / Pydantic dont les champs `Enum` listent **tous** les `id` de chaque axe.
3. Appeler le LLM avec ce schéma en **sortie contrainte** (`format` Ollama / JSON Schema).
4. Valider côté Python. Rejeter toute sortie hors schéma.

### 7.2 Schéma logique (pseudo-Pydantic)

```python
# Les Enum sont générés dynamiquement depuis taxonomy.json
class PageTags(BaseModel):
    organisation: OrganisationId          # enum de tous les id organisation
    publics: list[PublicId]               # min_length=1
    types_document: TypeDocumentId
    offre_etudes: list[OffreEtudesId]     # peut être vide
    title: str
    resume_markdown: str
```

### 7.3 Prompt système (exigences)

- Analyser uniquement le Markdown fourni.
- Choisir les tags **uniquement** parmi les ids autorisés.
- Pour `organisation` : le nœud le plus spécifique plausible (ex. faculté plutôt que `universitaet` si évident).
- Pour `types_document` : un seul type principal.
- Pour `offre_etudes` : laisser vide si la page n’est pas clairement liée à un cycle/forme.
- Résumé fidèle, concis, structuré (`##`, listes).
- Température basse (0.0–0.2).
- Tronquer l’entrée Markdown si besoin (ex. 80k caractères) **après** extraction ; pour les très longs PDF, stratégie v1 = tête + table des matières + queue si disponibles ; v2 = map-reduce (hors scope v1 obligatoire).

### 7.4 Entrée LLM

Ne pas envoyer toute la taxonomie en prose à chaque fois si le JSON Schema Enum suffit. Option utile : joindre en annexe courte la liste `id — label_fr` pour aider le modèle, **sans** permettre d’autres valeurs.

### 7.5 Enrichissement taxonomie (hors v1, prévu)

Plus tard : si le crawl révèle une institution absente, proposer un nœud candidat (`status: proposed`) dans un fichier séparé `taxonomy.proposals.json`, **sans** l’écrire dans `taxonomy.json` automatiquement.

---

## 8. Interface CLI attendue

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

Sous-commandes utiles :

| Commande | Rôle |
|---|---|
| `run` | Crawl + digest bout en bout |
| `crawl-only` | Remplit `raw/` + `crawl.json` sans LLM |
| `digest-only` | Traite les pages `fetched` en attente |
| `validate` | Vérifie `crawl.json` + digests vs taxonomie |

Codes de sortie : `0` ok, `1` erreur config, `2` échecs partiels (run terminé avec pages en erreur).

---

## 9. Organisation du code suggérée

```text
uzh_data_crawler/
  taxonomy.json
  browser/                 # existant — ne pas casser
  docs/
    SPEC-pipeline-crawl-resume-tags.md
  src/uzh_crawler/
    __init__.py
    cli.py
    taxonomy.py            # load, enums dynamiques, validate tags
    crawl/
      frontier.py
      fetch.py
      extract.py           # html/pdf → markdown
    digest/
      llm_ollama.py
      schema.py
      prompt.py
    store/
      crawl_json.py
      cache_sqlite.py
      paths.py
  tests/
    test_taxonomy_tags.py
    test_url_normalize.py
    test_schema_roundtrip.py
  pyproject.toml
```

---

## 10. Critères d’acceptation (v1)

1. `uzh_crawler run --seed https://www.uzh.ch/de.html --max-pages 30` produit un `crawl.json` valide et ≥ 1 digest.
2. Aucun digest `digest_ok` ne contient un tag hors `taxonomy.json`.
3. Relancer la même commande sans changement de pages : majorité de cache hits (304 ou hash), pas de réappel LLM inutile.
4. Changer `--model` force le retraitement.
5. Une page hors hôte est `out_of_scope`, pas digérée.
6. Les tests unitaires couvrent : chargement taxonomie, validation tags, normalisation URL, parsing du schéma LLM (fixture JSON).
7. Le browser `browser/` et Pages continuent de fonctionner sans modification obligatoire.

---

## 11. Contraintes opérationnelles Mac

- Ollama doit tourner localement avant `run`.
- Laisser de la marge RAM : poids modèle + KV cache &lt; ~90 % de la mémoire utile ; avec 48 Go, un 9B Q4 + 2 workers est confortable ; 4 workers LLM seulement si `OLLAMA_NUM_PARALLEL` est configuré.
- Variables utiles :
  - `OLLAMA_NUM_PARALLEL=2`
  - `OLLAMA_FLASH_ATTENTION=1` (si supporté)

---

## 12. Sécurité & éthique

- User-Agent identifiable (ex. `askUZH-TaxonomyCrawler/0.1 (+local; research)`).
- Pas de contournement de paywall / login.
- Pas de stockage de secrets dans le repo.
- Sorties destinées à un usage interne askUZH / cartographie web UZH.

---

## 13. Ordre d’implémentation recommandé

1. `taxonomy.py` + tests de validation des tags.
2. Extracteurs HTML/PDF → Markdown + tests fixtures.
3. Crawler async borné + écriture `crawl.json` / `raw/`.
4. Client Ollama + schéma Pydantic dynamique + un digest sur fixture locale.
5. Cache SQLite (hash + etag).
6. CLI `run` / `crawl-only` / `digest-only`.
7. Fallback JS optionnel.
8. (Plus tard) propositions d’enrichissement taxonomie.

---

## 14. Définition de terminé

Un livrable v1 est **terminé** quand un autre développeur peut, sur le Mac M3 Max 48 Go avec Ollama et `qwen3.5:9b` (ou modèle équivalent documenté dans le README), exécuter une commande unique qui remplit `runs/<id>/` avec crawl + résumés + tags fermés strictement alignés sur `taxonomy.json`, et reprendre sans retraiter l’identique.

---

## 15. Références internes

- Taxonomie : [`taxonomy.json`](../taxonomy.json)
- Browser : [`browser/index.html`](../browser/index.html)
- Skill de cartographie amont (protocole, pas code de crawl) : `uzh-metadata-explorer` (méthode / vagues) — source du squelette de termes uniquement.
