# Scope

The scope is set by `crawl` and recorded in the run directory. `scrape` reuses it.

The crawl is breadth-first and never leaves `uzh.ch`. A URL outside that domain, or on a UZH host that is not the host of a start page, is recorded as `out_of_scope` and is not requested. With `--allow-subdomains`, every `*.uzh.ch` host is inside the host scope.

`www.example.uzh.ch` and `example.uzh.ch` are the same site, for the host check and for prefixes. A redirect from one to the other therefore stays in scope.

## Path prefixes

`--include-prefix HOST PREFIX` confines one host. A prefix covers the exact path, or that path followed by `/`. It does not cover a neighbour that merely starts with the same letters: `/de/studies` accepts `/de/studies/bachelor.html` and refuses `/de/studies-extra`.

The index file and the folder are two prefixes. `/de/studies` does not cover `/de/studies.html`. Give both when the entry page is the `.html` file and the linked pages live in the folder.

`/dam/` is the file repository of the CMS, shared by the whole host. Adding it lets in the documents linked from the section. Their links are not followed.

An empty prefix, or one reduced to `/`, is refused: it would cover the whole host. A prefix must start with `/` and cannot contain `?`, `#` or `\`. The host must be under `uzh.ch` and is stored without `www`. A host with no prefix is not confined.

`.` and `..` segments are resolved before the comparison, so a `../` cannot step out of the prefix. A path containing `%2f` or `%5c` is refused.

A URL on the same site that misses the prefix, or that contains an `--exclude-path` text, is not recorded at all. Only a host that is really out of scope becomes an `out_of_scope` line. Two redirect cases also produce `out_of_scope` for the requested URL:

- the final response is on a host outside the scope, with the error `redirected outside the crawl scope: <url>`;
- the final response is in scope for the host but outside the section, with the error `redirect left the section`.

`--exclude-path` compares its text, ignoring case, with the path and with the whole URL. If the text is itself a URL, only its path is used. The three exclusions used for the study sections are `/intranet`, `/impressum` and `/search`. They are not active unless you pass them.

## URL identity

Before anything else, a URL is normalized: scheme and host in lower case, fragment removed, default port removed, repeated slashes collapsed, advertising and analytics parameters (`utm_*`, `fbclid`, `gclid` and similar) removed, query sorted. The path keeps its case. A URL with a user name or password is refused.

To decide whether a URL is already queued, a second key also drops the whole query, the trailing slash, and a final `/page/N`, `/seite/N` or `/pg/N` segment. The stored page keeps the first normalized URL that was met.

Images, CSS, JavaScript, source maps, audio, video, archives and fonts are neither listed nor downloaded. `mailto:`, `tel:` and `javascript:` links, empty anchors, and links that are unrendered templates (such as `[%=href%]`) are ignored.

## How the list is built

`crawl` builds the list of URLs from two sources.

| Mode | Option | Requests | The list contains |
| --- | --- | --- | --- |
| Sitemaps and links | default | One per HTML page | What the sitemaps declare, plus every page and document reached by a link |
| Sitemaps only | `--sitemap-only` | A handful | What the sitemaps declare |
| Links only | `--no-sitemap` | One per HTML page | What can be reached by links from the start URLs |

The default is the reliable one: a sitemap often omits documents and recent pages, and a link crawl alone misses pages nothing links to.

Only HTML pages are read during a crawl, because only they contain links. A link that ends in `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.odt`, `.ods`, `.odp`, `.rtf`, `.csv` or `.epub` is added to the list without a request. A URL with no such ending that turns out not to be HTML is listed too, and its body is not downloaded.

The HTML that the crawl reads is kept under `files/`, with its links under `raw/<hash>.links.txt`. `scrape` then works from that copy instead of downloading the page a second time.

`--max-pages` caps the HTML pages read. URLs found beyond the cap are still listed, as `discovered`; their own links are not followed.

`--max-depth N` works the same way: a page found N links away from a start URL is listed without a request, because its links would not be followed. A crawl only reads a page when it needs that page's links. Whether a listed page still exists is found out by `scrape`.

## Sitemaps

Each start origin is read in this order:

1. the `Sitemap:` lines of `/robots.txt`;
2. `/sitemap.txt`, `/sitemap.xml` and `/sitemap_index.xml`.

A text sitemap is one URL per line. An XML sitemap is a `urlset` or a `sitemapindex`; indexes are followed. At most 32 sitemap documents are read. An address that does not load is skipped and the crawl continues.

The URLs kept are those that pass the same host, prefix and exclusion rules as a link. They enter the queue at depth 0, like the start pages. With `--max-depth 0` they are listed and nothing is read.

## Study sections

`--study-sections` adds these eight pages and, for each, the prefix of the `.html` file, the prefix of the folder, and `/dam/`:

- <https://phil.uzh.ch/de/studium.html>
- <https://www.phil.uzh.ch/de/fakultaet.html>
- <https://www.oec.uzh.ch/de/faculty.html>
- <https://www.oec.uzh.ch/de/studies.html>
- <https://www.es.uzh.ch/en/studies.html>
- <https://www.econ.uzh.ch/de/study.html>
- <https://www.df.uzh.ch/en/studies.html>
- <https://www.ipz.uzh.ch/de/studium.html>

Prefixes of the same host are merged, so `phil.uzh.ch` gets both `/de/studium` and `/de/fakultaet`, and `oec.uzh.ch` gets both `/de/faculty` and `/de/studies`. Any additional `--seed` or `--include-prefix` is added to this set.

## Robots, rate and volume

`robots.txt` is respected by `crawl` and `scrape` unless `--ignore-robots` is passed. A forbidden URL is `failed`, with the error `disallowed by robots.txt`, and is checked again on the next run. When a host has no `robots.txt`, or the file cannot be loaded, everything on that host is allowed.

`robots.txt` is downloaded once per host and serves both for the access rules and for the sitemap addresses it declares. `scrape` asks it only before a download: a file that the crawl already stored was allowed when the crawl fetched it.

Requests to one host are paced by a token bucket. The host receives a first burst of 20 requests at once (`--burst`); after that, requests are released at 3 per second (`--rate-limit`). A host left alone earns its burst back at the same rate, so the average over a long crawl never exceeds the rate. Up to 20 requests run in parallel (`--crawl-concurrency`). A body larger than 20 MB is refused. HTTP 401 and 403 give `blocked_auth`; any other status outside 2xx gives `failed`.

## What scrape stores and extracts

`scrape` keeps the bytes of every successful download under `files/`, whatever the format. What it can turn into text depends on the media type of the response, compared exactly:

| Kind | Recognized by | Text |
| --- | --- | --- |
| HTML | `text/html`, `application/xhtml+xml`, or an untyped body that starts like HTML | Converted to Markdown |
| PDF | `application/pdf`, a `.pdf` URL with no type, or a body starting with `%PDF-` | Extracted page by page, pages separated by an empty line |
| Word | The `.docx` media type, or a zip file whose URL ends in `.docx` or `.docm` | Headings, paragraphs, lists and tables, in the order of the document |
| Excel | The `.xlsx` media type, or a zip file whose URL ends in `.xlsx` or `.xlsm` | One section and one table per sheet |
| Plain text | `text/plain` | Kept as it is |
| Anything else | Old `.doc` and `.xls` files, PowerPoint, XML feeds, … | None. The page is `empty` with the error `no text extractor for <type>` |

The four formats give Markdown of the same shape, so the summary stage does not need to know where a text came from:

- **HTML.** Links are extracted from the whole document, navigation included. The text comes from `main` or `article` when one exists, otherwise from the body. In both cases `script`, `style`, `nav`, `footer`, `header`, `aside`, `noscript`, `iframe` and `svg` are removed first. Raw HTML is never sent to the model.
- **PDF.** The text of each page, as the file stores it. Scanned pages, which hold a picture and no text, give nothing.
- **Word.** A paragraph in a heading style becomes a `#` heading of the same level, a numbered or bulleted paragraph becomes a `-` item, and a table becomes a Markdown table. A merged cell repeats its text in every column and row it covers. The fields of a form are skipped.
- **Excel.** Each sheet that holds something becomes `## <sheet name>` followed by a table. A formula gives its last computed value, a date is written as `2026-03-01`, a whole number without `.0`. Empty rows are dropped, and a cell holding an error such as `#DIV/0!` is left empty.

A Word, Excel or PDF file that cannot be opened makes the page `failed`, with the cause in `error`.

The conversion runs in several processes at once, one per processor, when a scrape has 8 documents or more. [Scrape performance](scrape-performance.md) explains how, and how fast it is.

Useful text is at least 200 characters; below that, the page is `empty` and is not summarized. With `--js-fallback`, one Playwright attempt takes place before that decision.

A URL given to `scrape` on the command line is scraped even if it is outside the prefixes of the run; it only has to be under `uzh.ch`. If it redirects to another site it becomes `out_of_scope`.
