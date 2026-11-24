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

Ollama must answer on `http://localhost:11434` with the model pulled; check with `curl -s localhost:11434/api/tags`. The default model is `qwen3.5:9b`. If Ollama is down the command exits with code 1 before any call.

Count the pages to do first (`fetched` in `crawl.json`). A summary takes a few seconds to a minute; over a hundred pages, run the command in the background and follow the `[summarize n/total]` lines.

## What it writes

`summaries/<hash>.md`: a YAML header (`title`, `source`, `content_hash`, `model`, `lang`) and the summary under `## Summary`, `## Résumé` or `## Zusammenfassung`. The page becomes `summarized`, or `summary_error` with the cause in `error` after two failed attempts.

The summary is the only thing the classification stage sees. It is prompted to name the publishing unit, the audience, the kind of document and the study level whenever the text gives them. When you spot-check summaries, check those four points first.

## When a summary is redone

A summary is reused while the Markdown, the model (`--model`) and the language (`--lang`) are all unchanged. Changing any of them redoes the summary, and the tags made from it are dropped until the next `classify`. Naming a URL on the command line redoes it regardless.

A document longer than 80,000 characters is cut to its beginning, a list of its headings, and its end.

## Done when

No page is left `fetched`. Report the number of summaries written, the number reused, and each `summary_error` with its cause. Read three summaries against their `raw/` Markdown before reporting, and say what you found.
