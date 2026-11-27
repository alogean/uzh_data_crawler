---
name: uzh-optimize
description: Make a stage of the UZH pipeline faster — measure it with its benchmark, try one idea at a time, keep what the numbers support, record the rest. Use when asked to optimise, speed up, benchmark or profile crawl, scrape or summarize, or before changing code under src/uzh_crawler/ for speed. Holds the record of what already worked and what did not.
---

# Optimize: make a stage faster, and remember what was tried

Three stages have been optimised so far: `crawl` (4.64 s → 0.24 s) and `scrape` (1.26 s → 0.17 s), twenty measured iterations each, and `summarize` (381 s → 30 s), fifty. This skill holds the method they shared and what each one taught.

| File | Read it when |
| --- | --- |
| This file | Always: the method, and the lessons that held for both stages |
| [crawl.md](crawl.md) | Before touching the speed of `crawl` |
| [scrape.md](scrape.md) | Before touching the speed of `scrape` |
| [summarize.md](summarize.md) | Before touching the speed of `summarize`, its prompt or its model |

The stage files are the memory: what is in the code and why, what was rejected and must not be retried as is, the traps met on the way. Read the one for your stage before proposing an idea. The full story with every measurement, written for a human reader, is in `docs/crawl-performance.md`, `docs/scrape-performance.md` and `docs/summarize-performance.md`.

## The loop

1. **Have a benchmark with a guard.** A fixed task, the real command, the median of at least 7 runs of the whole process, and a check that the result is the same as the reference. Without the guard a change can get faster by doing less. `crawl` and `scrape` have theirs:

   ```bash
   .venv/bin/python benchmarks/crawl_speed.py      # must list exactly the expected URLs
   .venv/bin/python benchmarks/scrape_speed.py     # must give the same words; reports WRONG otherwise
   .venv/bin/python benchmarks/summarize_speed.py  # needs Ollama; must keep the facts of the reference
   ```

   A stage without a benchmark gets one first, written before any change so that the reference comes from the untouched code.
2. **Measure today's median first.** Numbers move with the machine and, for anything that downloads, with the site and the day. Compare only numbers taken in the same session.
3. **Find where the time goes** (`--profile`; for scrape also `--by-kind`). Pick the largest part.
4. **Make one change. Run the tests, then the benchmark.**
5. **Keep it only if the median moves by more than the noise** (about 0.02 s for crawl, 0.005 s for scrape) **and the guard passes.** Otherwise revert it completely, including any switch added for the experiment.
6. **Record it either way**: a row in the table of the stage's performance page under `docs/`, and `--label "<n> <idea>"` to append the result to `benchmarks/results.jsonl`. When the idea taught something a future attempt needs — a rejected approach, a trap, a new limit — add it to the stage file of this skill in the same piece of work.

When the guard fails, read the lost and added words or URLs it prints before deciding. The reference changes only when the new result is better, for a reason you can state.

## What held for both stages

**Paid off, in this order of size:**

- **Look before optimising.** Both times the largest cost was not the expected one: the crawl spent 3.75 s of 4.64 s waiting for its own rate limiter, and HTML, the lightest format, was the most expensive part of the scrape.
- **Replace a parser written in Python by one written in C or Rust, called from Python.** This is the most reliable gain: 2 to 15 times on the part it touches (lexbor for HTML, `html-to-markdown`, pdfium, calamine).
- **Do not do work whose result is not used.** A page whose links will not be followed, twenty copies of one `robots.txt`, an HTTP client that sends no request, an HTML page parsed twice.
- **Load a library only when it is needed.** Imports and start-up were a quarter of a short command in both stages.
- **Processes for work that needs the processor**, started by `fork`, one per processor, and only when there is enough work to share.

**Never paid, do not retry without a new reason:**

- **Tuning the runtime**: another event loop, another HTTP client or protocol version, Python's isolated mode, a console script instead of `python -m`, caching small pure functions. All within the noise or slower.
- **Clever scheduling of the workers**: largest files first, batches, splitting one document between workers, preloading libraries before the fork. All within the noise or slower.
- **Rewriting a stage in another language.** Once the hot parts are C or Rust libraries, what is left in Python is a fixed 0.06 to 0.1 s per command that does not grow with the run. A second implementation of the scope rules, the manifest and resuming costs far more than that.

**Is not a target:**

- **Politeness.** A host receives a burst of 20 requests, then 3 per second. Any run that downloads more than the burst from one host is paced by this, and it is a choice, not a cost. Do not raise the defaults to improve a number; the user decides the load put on the UZH servers.

## Done when

The stage's performance page has a row for every idea tried, kept or not; the stage file of this skill says what a future attempt must know; the tests pass and the benchmark reports no wrong run; the median you report was measured in this session, with the one it is compared to.
