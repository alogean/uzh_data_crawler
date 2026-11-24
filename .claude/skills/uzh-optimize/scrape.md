# Scrape: what was tried

State after twenty iterations, measured with `benchmarks/scrape_speed.py` on 55 documents (24 HTML, 14 PDF, 10 Word, 7 Excel) and a 16-processor machine. Every measurement is in `docs/scrape-performance.md`; add a row there for each new idea.

| Measure | Before | After |
| --- | --- | --- |
| 55 documents already on disk (`convert`, the headline task) | 1.26 s | 0.17 s |
| Conversion alone, one processor | 0.94 s | 0.23 s |
| 1,100 documents (`--copies 20`) | 5.5 s with one worker | 0.65 s with 16 |
| 55 documents downloaded and converted (`--task live`) | 1.93 s | 1.88 s |

## What is in the code, and why

| Choice | Gain | Where |
| --- | --- | --- |
| HTML: `selectolax` (lexbor) cuts the page to `main` or `article` and removes navigation; `html-to-markdown` (Rust) writes the Markdown; one parse serves links and text | 24 pages: 347 ms → 30 ms | `scrape/extract.py`, `crawl/links.py` |
| PDF: `pypdfium2` instead of PyMuPDF | 14 files: 315 ms → 142 ms | `extract_pdf` |
| Word: own reader, `zipfile` and `lxml` over `word/document.xml` and `word/styles.xml`, instead of `python-docx` | 10 files: 164 ms → 50 ms | `extract_docx` |
| Excel: `python-calamine` (Rust) instead of `openpyxl` | 7 files: 82 ms → 10 ms | `extract_xlsx` |
| HTTP client built on the first request; `robots.txt` asked only before a download | 0.437 s → 0.382 s | `fetch.LazyClient`, `scraper.py` |
| Conversion in worker processes, one per processor, at most one per 4 documents | 0.382 s → 0.249 s | `scraper._worker_pool` |
| Workers started by `fork` rather than `spawn` | 0.249 s → 0.181 s | `scraper._worker_pool` |
| The main process loads no parsing library; workers start empty and load what their formats need | 0.178 s → 0.164 s | lazy imports in `extract.py` |
| Workers read the stored file and write `raw/<hash>.md` themselves; only a path goes in and a small record comes back | 0.171 s → 0.167 s, and memory stays bounded on large runs | `scraper.convert_document` |
| Run directory resolved once, not once per file | 1,100 documents: 0.90 s → 0.65 s, together with one worker per processor instead of 8 | `store/paths.safe_join` |

## What did not work

Do not retry these as they are. Each was measured against the code just before it. The last column is the explanation that fits the numbers; it was not itself measured.

| Idea | Result | Likely reason |
| --- | --- | --- |
| `html2text` for HTML | 35 ms against 8 ms for the Rust converter | Pure Python |
| `pdftotext` as an external program | 627 ms against 142 ms, 92% of the words found | One process start per file, and it drops text |
| `pdf_oxide` | 344 ms, 96% of the words found | Slower and less complete than pdfium |
| PyMuPDF inside the workers, once conversion was parallel | 0.247 s against 0.164 s | Still twice slower per file; its import is also heavy for each worker |
| `forkserver` start method | 0.235 s against 0.181 s for `fork` | Each worker still starts from an empty interpreter |
| Loading the parsing libraries before the fork so workers inherit them | 0.184 s against 0.179 s | The main process pays the imports alone, before any work is shared |
| Largest files first | Slower in two separate sessions | Sorting needs a `stat` per file, and the queue already balances |
| A long PDF split into page ranges read by several workers | 0.179 to 0.215 s against 0.173 s | Each share opens and parses the whole file again |
| Batches of 4 to 32 small documents per task | 0.65 to 0.69 s against 0.66 s on 1,100 documents | Passing a path and a small record is already cheap |
| Killing the workers at exit instead of letting them finish | 0.171 s against 0.168 s | They are already idle |
| A rewrite in Rust or Go | Not built | The readers are already C and Rust; Python is a fixed 0.06 s per command |

## Traps

- **`html-to-markdown` defaults lose text.** It drops elements marked hidden, whose text the reference contains, and it renders inline `svg` as a data URI. Hence `hidden_content="all"` in the options, and `svg` in the list of removed elements. The full options are `bullets="-", compact_tables=True, extract_metadata=False, hidden_content="all"`; change one only with the benchmark open.
- **Word content controls (`w:sdt`) must not be followed.** They hold form placeholders such as "Click here to enter text". Following them made the benchmark report `WRONG`. Tracked insertions, hyperlinks, smart tags and simple fields are followed.
- **`fork` is only safe with a single thread.** The pool is created, and every worker started with a no-op task, before the first request creates a thread. When `threading.active_count()` is above 1, as in `run` after the crawl stage, the pool uses `spawn`: 0.07 s slower, same result. Do not force `fork`.
- **pdfium gives no blank line between the paragraphs of a page.** The words and their order match PyMuPDF; pages are still separated. This was accepted as the price of the gain. If paragraph structure turns out to matter to the summaries, the measured cost of going back is 0.08 s on the benchmark.
- **A worker that dies takes the pool with it.** A crash inside a C library, as opposed to a Python exception, breaks the `ProcessPoolExecutor`; the pages still waiting end `failed` and are retried by the next `scrape`. Not seen on real files so far.
- **An experiment switch is not a result.** Environment variables added to compare two variants were all removed. Leave none behind.
- **Measure the conversion with `--copies 20` too.** On 55 documents the run ends when its slowest file ends (a 31-page PDF, 0.05 s), so anything about the workers only shows on 1,100.

## What limits it now

- The longest single document, on a small run.
- Python start-up and worker start, about 0.06 s per command.
- The number of processors, on a large run: about 1,700 documents per second with 16.
- Politeness, as soon as files are downloaded: `www.oec.uzh.ch` serves 24 of the 55 benchmark URLs, more than the burst of 20, so the `live` task cannot go under 1.8 s. With `--burst 30` it takes 0.64 s, which shows the code is not the limit. Leave the defaults.

## Not tried

These are open, not rejected. None has a measurement.

- Reading the old `.doc` and `.xls` formats, which are stored and not read today. This is coverage, not speed.
- Rebuilding paragraphs from pdfium's character positions, to get the blank lines back without PyMuPDF.
- Threads on a free-threaded Python build instead of processes.
