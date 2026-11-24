---
name: uzh-scrape
description: Scrape UZH URLs — download each one, store the original file locally and convert it to Markdown (HTML, PDF, Word, Excel). Use for one URL, a few URLs, or everything a crawl listed.
---

# Scrape: download, store, convert to Markdown

Scraping takes URLs and puts two things on disk for each: the file as served, and its text as Markdown.

```bash
# Everything the crawl listed and that is not stored yet
.venv/bin/python -m uzh_crawler scrape --out runs/phil

# One or several URLs, with or without a crawl before
.venv/bin/python -m uzh_crawler scrape https://www.phil.uzh.ch/de/studium.html --out runs/phil
```

With URLs, the run directory may be new: the URLs become the run. Without URLs, the stage takes every `discovered` page of the run and retries the `failed` ones.

## What it reads

| Format | Markdown it gives |
| --- | --- |
| HTML | The text of `main` or `article`, without navigation, header and footer |
| PDF | The text of each page. A scanned PDF holds pictures and gives nothing |
| Word `.docx` | Headings, paragraphs, lists and tables. Form fields are skipped |
| Excel `.xlsx` | One `## <sheet name>` section and one table per sheet, formulas as their values |

Anything else is stored and not read: old `.doc` and `.xls` files, PowerPoint, images.

## What it writes

- `files/<hash>.<ext>`: the original bytes (`.html`, `.pdf`, `.docx`, `.xlsx`, …). `<hash>` is the first 16 hex characters of the SHA-256 of the URL; the page's `file_path` in `crawl.json` gives the exact name.
- `raw/<hash>.md`: the text as Markdown. This is what the summary stage reads.
- `raw/<hash>.links.txt`: the links of an HTML page.

## Statuses it sets

| Status | Meaning |
| --- | --- |
| `fetched` | Stored, with at least 200 characters of text |
| `empty` | Stored, but under 200 characters of text. For a format that is not read, the `error` says `no text extractor for <type>` |
| `blocked_auth` | HTTP 401 or 403 |
| `out_of_scope` | The URL redirected to another site |
| `failed` | Download error, or a PDF, Word or Excel file that cannot be opened; the cause is in `error` |

## Speed

Two things set the duration, and they are separate:

- **Downloading** is paced by politeness: a host receives 20 requests at once, then 3 per second. A thousand documents from one host take about five and a half minutes. Raise `--rate-limit` only when the user asks for it: it is the load put on the UZH servers.
- **Converting** uses every processor: with 8 documents or more, worker processes convert in parallel, about 1,700 documents per second on a 16-processor machine. HTML that the crawl already downloaded is read from `files/` without a new request, so a scrape right after a crawl only downloads the documents.

To make the scrape itself faster, load the `uzh-optimize` skill: it holds what was already tried, kept and rejected. The measurements are in `docs/scrape-performance.md`.

## Options that matter

- `--refresh` also asks the server again for pages already stored, with a conditional request. Use it to update an old run; without it a stored page is left alone.
- `--js-fallback` renders a page in a browser when its HTML holds under 200 characters of text. It needs the `js` extra and Playwright's Chromium.
- `--max-pages N` caps the downloads of this execution. The default is no cap.
- `--workers N` sets the number of conversion processes; the default is one per processor. Pass `--workers 1` when the machine must stay free for other work.
- `--rate-limit`, `--burst` and `--crawl-concurrency` set the pace of the downloads; the defaults are 3 requests per second per host after a burst of 20, and 20 in parallel.

## Done when

No page of the run is left `discovered`, apart from those beyond a `--max-pages` cap. Report the counts of `fetched`, `empty`, `failed` and `blocked_auth`, and the causes of the failures. Among the `empty` pages, say how many are in a format that is not read.
