---
name: uzh-crawl
description: Crawl a uzh.ch domain or section to list all its URLs, fast, without downloading the documents. Use for "which pages does this site have", an inventory of a domain, or as the first stage before scraping.
---

# Crawl: list the URLs of a UZH site

Crawling answers one question: which URLs does this site have. It writes the list and stores no document text.

```bash
.venv/bin/python -m uzh_crawler crawl phil.uzh.ch --out runs/phil --max-pages 0
```

The target is a domain (`phil.uzh.ch`), a section URL (`www.oec.uzh.ch/de/studies.html`), or several of them. Name the run directory after the site.

## Choosing how to crawl

- **Sitemap only** (`--sitemap-only`): a handful of requests, done in seconds. The list is whatever the site declares in its sitemaps. Start here to size a site.
- **Sitemap and links** (default): reads every HTML page for its links, so it also finds pages and documents the sitemap omits. This is the reliable list; it costs one request per HTML page.
- **Links only** (`--no-sitemap`): for a site whose sitemap is wrong or missing.

## Speed

Each host gets a burst of 20 requests at once (`--burst`), then 3 per second (`--rate-limit`). A site of up to twenty pages is listed in well under a second; beyond that, count one third of a second per HTML page. Listing from sitemaps costs a handful of requests whatever the size of the site.

`--max-pages` caps the HTML pages read and defaults to 500; pass `0` for a complete crawl. `--max-depth N` stops following links N levels from the start. A URL beyond the page cap or at the depth limit is still listed as `discovered`, without a request: the crawl reads a page only when it needs that page's links, and whether a listed page exists is settled by the scrape stage.

Raise `--rate-limit` only when the user asks for it: it is the load put on the UZH servers. How the speed was measured and tuned is in `docs/crawl-performance.md`.

## Staying inside a section

A domain is crawled whole. To confine it, give `--include-prefix HOST PREFIX` for both the index file and its folder (`/de/studies.html` and `/de/studies`), plus `/dam/` if the linked PDFs are wanted. `--exclude-path TEXT` drops URLs containing that text. `--study-sections` loads the eight preset study and faculty sections. `--allow-subdomains` follows every `*.uzh.ch` host from the start pages: use it with a page cap.

The scope is fixed when the run directory is created. A second `crawl` on the same `--out` must repeat the same targets and scope options, and then only rechecks and extends the list.

## What it writes

- `urls.txt`: every in-scope URL, one per line, sorted. This is the deliverable.
- `crawl.json`: the same URLs with a status. `discovered` is listed and in scope; `out_of_scope` is a link to another host; `blocked_auth` needs a login; `failed` carries its cause in `error`.
- `files/<hash>.html` and `raw/<hash>.links.txt`: the HTML it had to read and the links found in it, kept so the scrape stage does not download the page twice.

Documents (PDF, Office files) are listed from their links and never downloaded here.

## Done when

The command has exited and `urls.txt` exists. Report the number of URLs, the split by host and by file type, and the count of `failed` and `blocked_auth` pages with their causes. Exit code 2 means at least one URL failed for a reason other than `robots.txt`.
