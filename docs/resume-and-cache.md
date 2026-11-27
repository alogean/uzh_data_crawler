# Resume and cache

Every command is resumable: run it again on the same `--out` and it continues. `crawl.json` records the status of each URL, and `finished_at` stays empty until a command has finished. An interruption leaves on disk everything already written; `crawl` and `scrape` save the manifest every two seconds, so at most the last two seconds of statuses are lost and redone.

Each command works out what is left to do from the statuses:

| Command | Takes pages that are | Skips |
| --- | --- | --- |
| `crawl` | In the queue from the start URLs, sitemaps and links | `blocked_auth` and `out_of_scope` |
| `scrape` | `discovered` or `failed` | Pages already stored, unless `--refresh` |
| `summarize` | `fetched` or `summary_error`, or whose summary is out of date | `empty`, and current summaries |
| `classify` | `summarized` or `tag_error`, or whose tags are out of date | Current tags |

## Scope of a run

The first `crawl` fixes the scope of a run directory. A later `crawl` or `run` on the same directory is refused, with exit code 1, when the start URLs are not the same, or when the prefixes, the exclusions or the sitemap choice differ. Changing `--max-pages` does not block a resume.

`scrape`, `summarize` and `classify` take the scope from the directory. Each command rewrites only its own settings in `crawl.json`: `summarize` records its model and language and leaves the crawl limits alone.

## HTTP cache

`crawl` sends `If-None-Match` and `If-Modified-Since` for an HTML page it has already read, when the server gave those headers. On a 304 the links are read back from `raw/<hash>.links.txt` and nothing is downloaded. On a 200 with different bytes, a page that was already scraped goes back to `discovered`, so the next `scrape` reads the new copy.

`scrape` only asks again for stored pages with `--refresh`, and then with the same conditional headers. A 304 keeps everything. A 200 is extracted again; if the resulting Markdown is identical, the summary and tags are kept.

A host that returns a `Last-Modified` equal to the time of the request never produces a 304. The body is downloaded again, and the comparison of the Markdown decides.

`blocked_auth` and `out_of_scope` pages are not requested again. `failed` pages are, including a `robots.txt` refusal.

If a page that already has a stored copy fails on a later run, the copy and its status are kept, and the failure is written to the page's `error` field.

## When a model call is skipped

`runs/<id>/cache.sqlite` records what each result was made from. It has two tables, each with one row per source URL.

| Table | A row is reused when all of these are unchanged | and this file still exists |
| --- | --- | --- |
| `summaries` | The hash of the Markdown, the summary model, the language | `summaries/<hash>.md` |
| `classifications` | The hash of the summary, the classification model, the fingerprint of `taxonomy.json` | `digests/<hash>.md` |

The two stages are cached separately, and that is what makes a change cheap:

| You change | Redone |
| --- | --- |
| `taxonomy.json` (any byte, whitespace included) | Tags only |
| The classification model | Tags only |
| The summary model or `--lang` | Summaries, then the tags made from them |
| The page content | Summary, then tags, for that page |

A new version of a prompt or of the cleaning does not make summaries out of date. Naming a URL on the command line of `summarize` or `classify` redoes it whatever the cache says.

Before a model call, the previous result is cleared and the manifest is written: `summarize` puts the page back to `fetched` and drops its summary and tags, `classify` puts it back to `summarized` and drops its tags. A crash during the call therefore leaves the page to be redone, never with tags that belong to an older summary or taxonomy.

## Runs made by version 1

A run directory made before the four-stage split has `schema_version` 1 in `crawl.json`. It is upgraded in memory when a command opens it, and written back as version 2:

- the crawl is kept: stored Markdown, links, validators;
- every `digest_ok` and `digest_error` page goes back to `fetched`, because version 1 made the summary and the tags in one call and left no summary to classify from. `summarize` and `classify` redo them;
- Word and Excel files, which version 1 stored as if they were HTML, go back to `discovered` so that `scrape` handles them properly;
- there is no original under `files/` for the kept pages. `scrape --refresh` downloads them if you want the originals.

Version 1 only read sitemaps when prefixes were set. If such a run was made without them, pass `--no-sitemap` to `crawl` to match its recorded scope.
