---
name: uzh-scrape
description: Scrape UZH URLs — download each one, store the original file locally and extract its text as Markdown. Use for one URL, a few URLs, or everything a crawl listed.
---

# Scrape: download and store

Scraping takes URLs and puts two things on disk for each: the file as served, and its text.

```bash
# Everything the crawl listed and that is not stored yet
.venv/bin/python -m uzh_crawler scrape --out runs/phil

# One or several URLs, with or without a crawl before
.venv/bin/python -m uzh_crawler scrape https://www.phil.uzh.ch/de/studium.html --out runs/phil
```

With URLs, the run directory may be new: the URLs become the run. Without URLs, the stage takes every `discovered` page of the run and retries the `failed` ones.

## What it writes

- `files/<hash>.<ext>`: the original bytes (`.html`, `.pdf`, `.docx`, …). `<hash>` is the first 16 hex characters of the SHA-256 of the URL; the page's `file_path` in `crawl.json` gives the exact name.
- `raw/<hash>.md`: the text as Markdown. For HTML it comes from `main` or `article`, without navigation, header and footer. This is what the summary stage reads.
- `raw/<hash>.links.txt`: the links of an HTML page.

## Statuses it sets

| Status | Meaning |
| --- | --- |
| `fetched` | Stored, with at least 200 characters of text |
| `empty` | Stored, but under 200 characters of text. For a Word or Excel file the `error` says `no text extractor`: the file is kept under `files/`, its text is not read |
| `blocked_auth` | HTTP 401 or 403 |
| `out_of_scope` | The URL redirected to another site |
| `failed` | Download or PDF error; the cause is in `error` |

## Options that matter

- `--refresh` also asks the server again for pages already stored, with a conditional request. Use it to update an old run; without it a stored page is left alone.
- `--js-fallback` renders a page in a browser when its HTML holds under 200 characters of text. It needs the `js` extra and Playwright's Chromium.
- `--max-pages N` caps the downloads of this execution. The default is no cap.
- `--rate-limit` and `--crawl-concurrency` set the pace; the defaults are 3 requests per second per host and 20 in parallel.

HTML that the crawl already downloaded is read from `files/` without a new request, so a scrape right after a crawl only downloads the documents.

## Done when

No page of the run is left `discovered`, apart from those beyond a `--max-pages` cap. Report the counts of `fetched`, `empty`, `failed` and `blocked_auth`, and the causes of the failures.
