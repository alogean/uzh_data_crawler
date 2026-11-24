# Specification — UZH crawl → Markdown summary → closed tags pipeline (local LLM)

> **Status.** This is the original v1 brief, translated from French and kept for reference. The tool has since been rebuilt as an agent with four separate stages (crawl, scrape, summarize, classify). Where this document and the code differ, the pages listed in [`docs/README.md`](README.md) describe the current behaviour. The main differences are listed in [section 16](#16-differences-from-the-implementation).

This document is for an agent or developer in charge of **implementing** the pipeline. It is self-contained: do not rely on any chat history.

**Repository:** `https://github.com/alogean/uzh_data_crawler`  
**Working branch:** `taxonomie-uzh`  
**Live taxonomy (read-only):** `https://alogean.github.io/uzh_data_crawler/`  
**Source taxonomy file:** [`taxonomy.json`](../taxonomy.json) at the repository root  
**Reference target machine:** MacBook Pro Apple M3 Max, **48 GB** of unified memory

---

## 1. Goal

Build a CLI tool (and possibly a library later) that:

1. starts from **one seed URL** (a UZH domain or subdomain);
2. **crawls** the allowed scope and records every URL discovered;
3. for each page (or document) kept, **extracts the text** as Markdown;
4. produces a **Markdown summary** with a **local LLM**;
5. assigns **closed tags** taken exclusively from `taxonomy.json` (no invented terms);
6. persists everything in files that can be versioned (JSON + Markdown), with **resume** and a **cache** so identical content is not processed twice.

The core purpose is not "a generic scraper": it is **tagging the UZH web with the askUZH taxonomy** that is already defined.

---

## 2. Non-goals (v1)

- Do not replace the existing taxonomy browser (`browser/`).
- Do not enrich `taxonomy.json` automatically in v1 (no nodes added during the crawl). Tags point only to `id` values already present.
- No RAG or vector index in v1.
- No cloud service; everything runs locally (network only to fetch UZH pages and to call Ollama or LM Studio on localhost).
- Do not crawl outside the UZH scope (hospitals outside `uzh.ch`, ETH, personal sites outside the scope, and so on), except to flag such URLs explicitly as "out of scope".

---

## 3. Existing artefacts (to reuse)

| Artefact | Role |
| --- | --- |
| `taxonomy.json` | Closed vocabulary with 4 axes, trilingual terms, definitions, `status: seed`, `sources[]` |
| `browser/` | Single-page app for browsing the taxonomy (do not break it) |
| `index.html` | Redirect to `browser/` for GitHub Pages |

### 3.1 Structure of `taxonomy.json` (contract)

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

**Axes and tagging cardinalities (v1):**

| Axis `id` | Meaning | Cardinality per URL |
| --- | --- | --- |
| `organisation` | Who owns or publishes the page | **exactly 1** node id |
| `publics` | Who the page is for | **1 to N** ids |
| `types_document` | Genre of content (normative texts are a **subtype**) | **exactly 1** id |
| `offre_etudes` | Study level or programme form, when relevant | **0 to N** ids (often 0) |

The tags stored are the node **`id`** values, never a free label.

Current state (order of magnitude): organisation ~30, publics ~10, types_document ~17, offre_etudes ~9 nodes. The skeleton is deliberately incomplete (no named institutes).

---

## 4. Target architecture

Decouple **collection** from the **LLM**. The LLM is the bottleneck; the crawl must be fast and asynchronous.

```text
[seed URL]
    │
    ▼
┌───────────────────┐     URLs + raw Markdown        ┌────────────────────┐
│  Crawler / Parser │ ─────────────────────────────► │  Store (disk)      │
│  HTTP + HTML/PDF  │                                │  raw/ + queue + db │
└───────────────────┘                                └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │  Local LLM workers │
                                                     │  summary + tags    │
                                                     └─────────┬──────────┘
                                                               │
                                                               ▼
                                                     ┌────────────────────┐
                                                     │  Outputs           │
                                                     │  crawl.json        │
                                                     │  digests/*.md      │
                                                     │  cache.sqlite      │
                                                     └────────────────────┘
```

### 4.1 Recommended technology choices (v1)

**Priority: Python as the orchestrator**, with native bindings where needed.

| Layer | v1 choice | Alternative |
| --- | --- | --- |
| CLI language | Python 3.11+ | Rust later, for volume or a standalone CLI |
| Async HTTP | `httpx` (HTTP/2) + `Semaphore` | `curl_cffi` if the TLS fingerprint is blocked |
| HTML → Markdown | `htmd-py` or `trafilatura` + conversion | `Docling` for complex PDF/HTML |
| PDF text | `pymupdf4llm` | `Docling` if scans or OCR are needed |
| Local LLM | **Ollama** API on `localhost:11434` | LM Studio (OpenAI-compatible) |
| Structured output | Pydantic v2 + Ollama `format=` / JSON Schema | |
| Cache | SQLite (`sqlite3`) + content hash | |
| Browser | **not by default**; Playwright only as a fallback when extraction yields &lt; 200 characters | |

**Recommended local LLM (48 GB Mac):**

- Default for summary and tagging: `qwen3.5:9b` (or an equivalent Qwen 3.x of about 9B available in Ollama) in 4-bit quantization / MLX.
- Alternative: `gpt-oss:20b` if the taxonomy is too ambiguous for the 9B model.
- **Disable thinking mode** (`think=False` or equivalent) for this use case.
- Force a high `num_ctx` (for example 32768): the Ollama default often truncates.

Do not load a 70B+ model for closed tagging: it is unnecessary and slower.

---

## 5. Crawl scope

### 5.1 Input

```text
--seed https://www.phil.uzh.ch/
--max-pages 500
--same-host | --allow-subdomains
```

v1 rules:

1. Normalize URLs (scheme, host, path, remove the `#` fragment, optionally tracking query parameters).
2. Stay on the seed's host, **or** on `*.uzh.ch` with `--allow-subdomains`.
3. Respect `robots.txt` unless the explicit flag `--ignore-robots` is given (default: respect).
4. Configurable rate limit (for example 2–5 requests per second per host).
5. Follow HTML `a[href]` links; also record the PDFs discovered.
6. Strict URL deduplication before fetching.
7. Optional maximum depth (`--max-depth`).

### 5.2 Exclusions (flag them, do not build logic for them)

- Researchers' personal sites outside the faculty CMS (a category not detailed here).
- News and one-off events: **to be crawled and tagged** (the `actualite` and `evenement` types exist in the taxonomy), but with no special business logic.
- Intranet / authentication: record the URL as `status: blocked_auth`, without endless retries.
- Domains outside `uzh.ch`: `status: out_of_scope` (except a PDF hosted along the same path, if a policy is defined).

### 5.3 Content extraction

For each URL fetched successfully:

1. If PDF → Markdown through a PDF extractor.
2. Otherwise HTML → Markdown (remove `script`, `style`, `nav`, `footer`, `header`, `aside` if the tool allows it).
3. If the useful Markdown is &lt; 200 characters → a single Playwright attempt (optional, flag `--js-fallback`), otherwise `status: empty`.
4. **Never** send raw HTML to the LLM.

Store the raw Markdown under:

```text
runs/<run_id>/raw/<url_hash>.md
```

---

## 6. Output data contracts

### 6.1 Crawl manifest — `runs/<run_id>/crawl.json`

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

Constraints:

- `tags.organisation`: string id ∈ organisation axis.
- `tags.publics`: non-empty list of ids ∈ publics axis.
- `tags.types_document`: string id ∈ types_document axis.
- `tags.offre_etudes`: list (may be `[]`).
- Any unknown id → **validation failure** (do not write `digest_ok`).

### 6.2 Markdown digest — `runs/<run_id>/digests/<hash>.md`

YAML frontmatter + summary body:

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

The summary is in **French** by default (configurable with `--lang fr|de|en`).

### 6.3 SQLite cache — `runs/<run_id>/cache.sqlite` (or a shared `data/cache.sqlite`)

Minimal table:

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

Two barriers:

1. **HTTP**: `If-None-Match` / `If-Modified-Since` → 304 = skip fetch and LLM.
2. **Content**: identical hash of the normalized Markdown + digest file present + same `model` + same `taxonomy_sha256` → skip the LLM.

If the model or the taxonomy hash changes → process again.

---

## 7. Closed tagging with the LLM (core)

### 7.1 Principle

1. Load `taxonomy.json`.
2. Build a JSON / Pydantic schema dynamically, whose `Enum` fields list **every** `id` of each axis.
3. Call the LLM with this schema as a **constrained output** (Ollama `format` / JSON Schema).
4. Validate on the Python side. Reject any output outside the schema.

### 7.2 Logical schema (pseudo-Pydantic)

```python
# The Enums are generated dynamically from taxonomy.json
class PageTags(BaseModel):
    organisation: OrganisationId          # enum of all organisation ids
    publics: list[PublicId]               # min_length=1
    types_document: TypeDocumentId
    offre_etudes: list[OffreEtudesId]     # may be empty
    title: str
    resume_markdown: str
```

### 7.3 System prompt (requirements)

- Analyse only the Markdown provided.
- Choose tags **only** among the allowed ids.
- For `organisation`: the most specific plausible node (for example a faculty rather than `universitaet` when it is obvious).
- For `types_document`: a single main type.
- For `offre_etudes`: leave empty if the page is not clearly tied to a study level or programme form.
- A faithful, concise, structured summary (`##`, lists).
- Low temperature (0.0–0.2).
- Truncate the Markdown input if needed (for example 80k characters) **after** extraction; for very long PDFs, the v1 strategy is head + table of contents + tail when available; v2 is map-reduce (outside the mandatory v1 scope).

### 7.4 LLM input

Do not send the whole taxonomy as prose every time if the JSON Schema Enum is enough. A useful option: attach a short appendix listing `id — label_fr` to help the model, **without** allowing other values.

### 7.5 Taxonomy enrichment (outside v1, planned)

Later: if the crawl reveals an institution that is missing, propose a candidate node (`status: proposed`) in a separate file `taxonomy.proposals.json`, **without** writing it into `taxonomy.json` automatically.

---

## 8. Expected CLI

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

Useful subcommands:

| Command | Role |
| --- | --- |
| `run` | Crawl + digest end to end |
| `crawl-only` | Fills `raw/` + `crawl.json` without the LLM |
| `digest-only` | Processes the pending `fetched` pages |
| `validate` | Checks `crawl.json` + digests against the taxonomy |

Exit codes: `0` ok, `1` configuration error, `2` partial failures (run finished with pages in error).

---

## 9. Suggested code organisation

```text
uzh_data_crawler/
  taxonomy.json
  browser/                 # existing — do not break
  docs/
    SPEC-crawl-summary-tags-pipeline.md
  src/uzh_crawler/
    __init__.py
    cli.py
    taxonomy.py            # load, dynamic enums, validate tags
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

## 10. Acceptance criteria (v1)

1. `uzh_crawler run --seed https://www.uzh.ch/de.html --max-pages 30` produces a valid `crawl.json` and ≥ 1 digest.
2. No `digest_ok` digest contains a tag outside `taxonomy.json`.
3. Running the same command again with no page changes: mostly cache hits (304 or hash), no needless LLM call.
4. Changing `--model` forces reprocessing.
5. A page on another host is `out_of_scope` and is not digested.
6. Unit tests cover: taxonomy loading, tag validation, URL normalization, parsing of the LLM schema (JSON fixture).
7. The `browser/` app and GitHub Pages keep working with no mandatory change.

---

## 11. Operational constraints on the Mac

- Ollama must be running locally before `run`.
- Leave RAM headroom: model weights + KV cache &lt; ~90 % of usable memory; with 48 GB, a 9B Q4 model + 2 workers is comfortable; 4 LLM workers only if `OLLAMA_NUM_PARALLEL` is configured.
- Useful variables:
  - `OLLAMA_NUM_PARALLEL=2`
  - `OLLAMA_FLASH_ATTENTION=1` (if supported)

---

## 12. Security and ethics

- Identifiable User-Agent (for example `askUZH-TaxonomyCrawler/0.1 (+local; research)`).
- No bypassing of paywalls or logins.
- No secrets stored in the repository.
- Outputs are for internal askUZH use and for mapping the UZH web.

---

## 13. Recommended implementation order

1. `taxonomy.py` + tag validation tests.
2. HTML/PDF → Markdown extractors + fixture tests.
3. Bounded async crawler + writing of `crawl.json` / `raw/`.
4. Ollama client + dynamic Pydantic schema + one digest on a local fixture.
5. SQLite cache (hash + etag).
6. CLI `run` / `crawl-only` / `digest-only`.
7. Optional JS fallback.
8. (Later) taxonomy enrichment proposals.

---

## 14. Definition of done

A v1 deliverable is **done** when another developer can, on the M3 Max 48 GB Mac with Ollama and `qwen3.5:9b` (or an equivalent model documented in the README), run a single command that fills `runs/<id>/` with crawl + summaries + closed tags strictly aligned with `taxonomy.json`, and resume without reprocessing identical content.

---

## 15. Internal references

- Taxonomy: [`taxonomy.json`](../taxonomy.json)
- Browser: [`browser/index.html`](../browser/index.html)
- Upstream mapping skill (a protocol, not crawl code): `uzh-metadata-explorer` (method / waves) — the source of the term skeleton only.

---

## 16. Differences from the implementation

Added after the brief was written. These are the points where the code went a different way; the detail is in the linked pages.

| Topic | In this brief | In the code |
| --- | --- | --- |
| Shape | One CLI pipeline | A Claude Code agent with four skills, each driving one command ([Taxon and its skills](agent.md)) |
| Stages | Crawl, which also stores the pages, then one digest step | Four: `crawl` lists URLs, `scrape` downloads, `summarize`, `classify` ([How a run works](README.md#how-a-run-works)) |
| Commands | `run`, `crawl-only`, `digest-only`, `validate` | `crawl`, `scrape`, `summarize`, `classify`, `run`, `validate`, `proposals` ([Command line](cli.md)) |
| Model calls | One call returns the summary and the tags | Two calls: the summary first, then the tags from the summary ([Tags](tags.md)) |
| Page statuses | `fetched`, `digest_ok`, `digest_error`, … | `discovered`, `fetched`, `summarized`, `summary_error`, `tagged`, `tag_error`, … ([Outputs](outputs.md)) |
| Stored files | Markdown only | The original bytes under `files/` as well |
| Manifest | `schema_version` 1 | `schema_version` 2; version 1 runs are upgraded when opened ([Resume and cache](resume-and-cache.md)) |
| Start URLs | One `--seed` | A domain argument or `--seed`, repeatable; `--study-sections` adds eight preset sections ([Scope](scope.md)) |
| Scope within a host | Whole host | `--include-prefix` and `--exclude-path` confine a host to sections |
| Discovery | Links only | Sitemaps and links by default; `--sitemap-only` and `--no-sitemap` choose one |
| `www` and bare host | Not addressed | Treated as one site |
| Same-site URL outside the section | Not addressed | Not recorded at all; only a foreign host becomes `out_of_scope` |
| HTML → Markdown | `htmd-py` or `trafilatura` | BeautifulSoup + `markdownify` |
| PDF text | `pymupdf4llm` | PyMuPDF directly |
| Content hash | BLAKE3 or SHA-256 | SHA-256, prefixed `sha256:` |
| 304 response | Skips fetch and LLM | Skips the download only; each model stage has its own cache |
| Cache table | One `documents` table | Two tables, `summaries` and `classifications` |
| `--max-pages` | A positive ceiling | `0` removes the ceiling |
| `robots.txt` refusal | Not addressed | `failed` with a fixed error, and does not cause exit code 2 on its own |
| Taxonomy enrichment (7.5) | Planned, with `taxonomy.proposals.json` | Implemented as `proposals hints`, `check` and `accept`; a person accepts each node ([Taxon and its skills](agent.md#taxonomy-proposals)) |
