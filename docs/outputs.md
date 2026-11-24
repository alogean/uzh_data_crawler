# Outputs

Everything lives in the `--out` directory. `runs/` is ignored by git.

```text
runs/<id>/
  crawl.json                 state of the run: one entry per URL        all stages
  urls.txt                   every in-scope URL, one per line           crawl
  files/<hash>.<ext>         the original bytes as served               crawl (HTML), scrape
  raw/<hash>.md              the text, as Markdown                      scrape
  raw/<hash>.links.txt       the links of an HTML page                  crawl, scrape
  summaries/<hash>.md        title and summary                          summarize
  digests/<hash>.md          summary and tags: the final result         classify
  cache.sqlite               what each model result was made from       summarize, classify
```

`<hash>` is the first 16 hexadecimal characters of the SHA-256 of the normalized URL. `<id>` is the name of the directory. Each page entry in `crawl.json` gives the exact paths of its files.

To read a result, open a file in `digests/`: the tags are in the YAML header and the summary follows. To get the list of URLs of a site, read `urls.txt`. `cache.sqlite` is described in [Resume and cache](resume-and-cache.md).

## crawl.json

The manifest has `schema_version` 2 and `kind` `crawl_run`. It holds `run_id`, `seed_url` (the first start URL), `started_at`, `finished_at`, the run configuration `config`, the counters `stats`, and the list `pages`. Timestamps are local time in ISO 8601 with the UTC offset. The file is replaced atomically each time it is saved.

Each page has these fields:

| Fields | Set by | Content |
| --- | --- | --- |
| `url`, `normalized_url`, `discovered_from`, `depth` | crawl | The URL (both fields hold the normalized form), the page or sitemap that named it, its distance from a start URL |
| `status`, `error` | every stage | See below. `error` is the cause of a failure, or a note on a page that kept its copy |
| `http_status`, `content_type`, `etag`, `last_modified`, `fetched_at` | crawl, scrape | What the server answered |
| `file_path` | crawl, scrape | The original under `files/` |
| `raw_path`, `content_hash` | scrape | The Markdown under `raw/` and `sha256:` plus its fingerprint, after normalizing line endings and trailing spaces |
| `title` | crawl, summarize | The HTML title, replaced by the model's title once summarized |
| `summary_path`, `summary_hash` | summarize | The summary file and its fingerprint |
| `digest_path`, `tags`, `missing_unit` | classify | The digest file, the tags, and the name of the publishing unit when no node names it |

`config` is written in parts: each command records its own settings and leaves the others.

| Keys | Written by | Content |
| --- | --- | --- |
| `seeds`, `allow_subdomains`, `include_prefixes`, `exclude_path_contains`, `discover_sitemaps` | crawl | The scope. `include_prefixes` maps a host without `www` to its list of prefixes |
| `max_pages`, `max_depth`, `sitemap_only` | crawl | Limits. `null` means no limit |
| `rate_limit`, `burst`, `crawl_concurrency`, `ignore_robots` | crawl, scrape | Request behaviour |
| `js_fallback` | scrape | |
| `summary_model`, `lang` | summarize | |
| `classify_model`, `taxonomy_path`, `taxonomy_sha256` | classify | The taxonomy file and the SHA-256 of its bytes |
| `num_ctx`, `temperature`, `llm_concurrency` | summarize, classify | Model call |

The counters in `stats` are recomputed on every save:

| Counter | Counts |
| --- | --- |
| `urls_discovered` | All entries in the list, `out_of_scope` ones included |
| `urls_fetched_ok` | Pages with a scraped copy: `fetched`, `empty`, `summarized`, `summary_error`, `tagged`, `tag_error` |
| `urls_failed` | Status `failed` |
| `urls_summarized` | Pages with a summary: `summarized`, `tagged`, `tag_error` |
| `urls_tagged` | Status `tagged` |

## Statuses

A page moves down this table as the stages run.

| Status | Set by | Meaning |
| --- | --- | --- |
| `discovered` | crawl | Listed and in scope. Not scraped yet, or changed since it was |
| `fetched` | scrape | Stored, with at least 200 characters of text. Waiting for a summary |
| `empty` | scrape | Stored, but with under 200 characters of text, or in a format with no text extractor. Not summarized |
| `summarized` | summarize | Summary written. Waiting for tags |
| `summary_error` | summarize | The model failed twice. Retried by the next `summarize` |
| `tagged` | classify | Tags valid and digest written. The end state |
| `tag_error` | classify | The model failed or produced refused tags twice. Retried by the next `classify` |
| `blocked_auth` | crawl, scrape | HTTP 401 or 403. Not requested again |
| `out_of_scope` | crawl, scrape | Host out of scope, or a redirect left the scope or the section. Not requested again |
| `failed` | crawl, scrape | Request error, unreadable PDF, or `robots.txt` refusal. Retried |

## Summary file

```markdown
---
title: "Studies"
source: "https://www.es.uzh.ch/en/studies.html"
content_hash: "sha256:…"
model: "qwen3.5:9b"
lang: en
---

## Summary

…
```

The section heading is `## Résumé`, `## Zusammenfassung` or `## Summary`, depending on `--lang`. The title is the one returned by the model, or else the HTML title, or else the URL.

## Digest

```markdown
---
title: "Studies"
source: "https://www.es.uzh.ch/en/studies.html"
content_hash: "sha256:…"
summary_hash: "sha256:…"
summary_model: "qwen3.5:9b"
model: "qwen3.5:9b"
taxonomy_sha256: "…"
tags:
  organisation: fakultaeten
  publics:
    - studierende_bachelor
  types_document: portail
  offre_etudes:
    - bachelor
---

## Summary

…
```

The body is the summary, copied from the summary file. `model` is the classification model. Tag ids are written as they are; a value that is not a plain identifier is written as a JSON string. An empty `offre_etudes` is written `offre_etudes: []`.

## validate

`validate` reads the run again without fetching or calling the model. For each page that has a summary it checks that:

- the file in `raw/` exists and its hash is the page's `content_hash`;
- the summary file exists and its hash is the page's `summary_hash`.

For each `tagged` page it also checks that:

- the tags in the manifest exist in the taxonomy and respect the cardinalities;
- the digest file exists, has a readable header, and carries the same tags;
- the `source`, `content_hash` and `summary_hash` of the header match the page;
- the `model` and `taxonomy_sha256` of the header match the run configuration.

It prints `validate: ok` when there is nothing to report. Otherwise it lists the problems on standard error and exits with code 2. A missing or unreadable `crawl.json` exits with code 1.

`validate` shows that the tags are well formed and current. It cannot tell whether they are the right ones.
