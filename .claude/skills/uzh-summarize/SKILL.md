---
name: uzh-summarize
description: Summarize scraped UZH documents with the local Ollama model. Use after scraping, for one URL or a whole run, or to redo summaries in another language or with another model.
---

# Summarize: one local model call per document

Summarizing reads the Markdown of a scraped document and writes a title and a structured summary. The taxonomy plays no part here.

```bash
# Every scraped document of the run that has no current summary
.venv/bin/python -m uzh_crawler summarize --out runs/phil --lang en

# Chosen URLs of the run, redone even if a summary exists
.venv/bin/python -m uzh_crawler summarize https://www.phil.uzh.ch/de/studium.html --out runs/phil
```

## Before starting

Ollama must answer on `http://localhost:11434` with the model pulled; check with `curl -s localhost:11434/api/tags`. The default model is `gemma4:e2b` (`ollama pull gemma4:e2b`), with 4 calls at a time. If Ollama is down the command exits with code 1 before any call; a model that is not pulled turns every page into `summary_error`.

`gemma4:e2b` is fast and faithful, but it can leave out the publishing unit or translate German terms badly. For fuller summaries in better French, at about five times the time, pass `--model qwen3.5:9b`.

Count the pages to do first (`fetched` in `crawl.json`). A summary takes about 2 to 10 seconds with the default model; over a hundred pages, run the command in the background and follow the `[summarize n/total]` lines.

## What it writes

`summaries/<hash>.md`: a YAML header (`title`, `source`, `content_hash`, `model`, `lang`) and the summary under `## Summary`, `## Résumé` or `## Zusammenfassung`. The page becomes `summarized`, or `summary_error` with the cause in `error` after two failed attempts.

The summary is the only thing the classification stage sees. It is prompted to name the publishing unit, the audience, the kind of document and the study level whenever the text gives them. When you spot-check summaries, check those four points first.

## When a summary is redone

A summary is reused while the Markdown, the model (`--model`) and the language (`--lang`) are all unchanged. Changing any of them redoes the summary, and the tags made from it are dropped until the next `classify`. Naming a URL on the command line redoes it regardless.

The model reads a cleaned copy of the Markdown (no images, link targets or repeated lines). Beyond 32,000 characters it gets the beginning, a list of the headings, and the end. A change of the prompt alone does not redo existing summaries: name the URLs to redo them.

## Done when

No page is left `fetched`. Report the number of summaries written, the number reused, and each `summary_error` with its cause. Read three summaries against their `raw/` Markdown before reporting, and say what you found.
