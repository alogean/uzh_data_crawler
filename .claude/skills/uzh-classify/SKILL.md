---
name: uzh-classify
description: Classify summarized UZH documents by tagging each summary with ids from taxonomy.json, using the local Ollama model. Use after summarizing, after taxonomy.json changed, or to check tags against the taxonomy.
---

# Classify: tag a summary with the taxonomy

Classifying reads a summary and chooses tags that are ids of `taxonomy.json`. The model's answer is constrained to those ids, so a tag outside the taxonomy cannot be stored.

```bash
# Every summary of the run that has no current tags
.venv/bin/python -m uzh_crawler classify --out runs/phil --taxonomy taxonomy.json

# Chosen URLs of the run, redone even if tags exist
.venv/bin/python -m uzh_crawler classify https://www.phil.uzh.ch/de/studium.html --out runs/phil
```

Ollama must be running, as for `uzh-summarize`.

## The four axes

| Axis | Question | Tags per document |
| --- | --- | --- |
| `organisation` | Who publishes it | exactly 1 |
| `publics` | Who it is for | 1 or more |
| `types_document` | What kind of document | exactly 1 |
| `offre_etudes` | Which study level or programme form | 0 or more |

## What it writes

`digests/<hash>.md` is the result for one document: the tags in the YAML header, the summary below. `crawl.json` holds the same tags in the page's `tags`. The page becomes `tagged`, or `tag_error` with the cause in `error` after two failed attempts.

When no `organisation` id names the publishing unit, the model picks a broader node and writes the unit's name in the page's `missing_unit`. Those names are the raw material for taxonomy proposals: `taxonomy hints --out <run>` groups them, and the `taxon-gate` skill turns them into proposals.

## When tags are redone

Tags are reused while the summary, the model and the bytes of `taxonomy.json` are all unchanged. Any edit to `taxonomy.json`, including accepting a proposal, redoes every page on the next `classify`; the summaries are kept. Plan for that time before changing the taxonomy.

## Checking

```bash
.venv/bin/python -m uzh_crawler validate --out runs/phil --taxonomy taxonomy.json
```

`validate` rereads every summary and digest and confirms that the tags exist in the taxonomy, that the manifest and the files agree, and that each digest was made from the current summary, model and taxonomy. It proves the tags are well formed, and says nothing about whether they are the right ones: for that, read digests.

## Done when

No page is left `summarized` and `validate` prints `validate: ok`. Report the tag counts per axis, each `tag_error` with its cause, and the result of reading five digests against their summaries.
