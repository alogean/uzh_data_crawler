---
name: taxon-gate
description: Read, check, change and export the UZH taxonomy (taxonomy.json) through its gate, the `taxonomy` command. Use to look up a node, propose adding, renaming, moving or retiring a node, turn a run's missing units into proposals, accept or reject proposals the user named, check the taxonomy's quality, export it as CSV, JSON, Markdown or SKOS Turtle, or change the gate's own code and rules.
---

# Taxon gate: the one way in and out of the taxonomy

The `taxonomy` command is the **gate** of `taxonomy.json`. Every read goes through `taxonomy tree`, `show` or `find`; every change goes through `taxonomy propose`, then a person's decision. The gate writes the three files itself: `taxonomy.json`, `taxonomy.proposals.json`, `taxonomy.history.jsonl`. Edit them only through it, never with Write or Edit, so that every change is checked, versioned and recorded.

```bash
G=".venv/bin/python -m uzh_crawler taxonomy"
$G tree --axis organisation          # the tree, with statuses
$G show mnf                          # one node: path, children, labels, history, pending proposal
$G find Philosophisches Seminar      # ids and labels, accents and case ignored
$G check                             # every checked rule, then the pending proposals
```

`--json` on `tree`, `show`, `find`, `pending` and `history` gives the same answer as JSON. `docs/taxonomy.md` explains the model, every rule and its source, the versions and the exports; read it when a rule's reason matters.

## Proposing a change

One proposal is one change to one node: `add`, `update` (labels, alt_labels, definition, scope_note, sources), `move` (new parent) or `deprecate` (retire, with `replaced_by`). The JSON shape of each is in `docs/taxonomy.md`, "The four kinds of change".

1. **Look before you write.** `taxonomy find` the unit's name and its abbreviation, and `taxonomy show` the intended parent. A node that already names the unit gets an `update` with an alternative label, not an `add`.
2. **Hold the proposal to the editorial rules:**
   - One node, one concept. An id is permanent: it is the unit's German name in lower case, words joined by `_`, with ä→ae, ö→oe, ü→ue, ß→ss. A better name goes into `labels`; an id is never renamed or reused.
   - Warrant: several pages of a run need the node, and the unit's own page (under `raw/`) gives its official name and its parent. That page goes in `sources`, example pages in `evidence`.
   - The parent passes the all-and-some test: everything about the child is about the parent, and the child relates to its parent as its siblings do ("is part of", or "is a kind of").
   - On `organisation` and `types_document`, a page must fit one sibling only. Where two could be confused, write a `scope_note`.
   - Labels are the official names in de, fr, en as the unit's pages give them; abbreviations and other names go to `alt_labels`. The definition is one sentence: what it is and where it sits, in the style of the existing nodes.
   - Retire with `deprecate` and name the successors in `replaced_by`. Move the children away first, in earlier proposals of the same batch.
   - The `reason` says why, with the number of pages and the run, in English: it becomes the node's history and its SKOS change note.
3. **Submit it** on standard input, one object or a list:

   ```bash
   .venv/bin/python -m uzh_crawler taxonomy propose <<'EOF'
   {"change": "update", "id": "mnf", "set": {"alt_labels": {"de": ["MNF"]}}, "reason": "12 summaries of runs/etudes-sections name the faculty MNF"}
   EOF
   ```

   `proposed: <id> (<change>)` means it is pending. A refusal names the rule that failed (`labels-complete`, `hierarchy-cycle`, …) and writes nothing: fix the JSON and submit again. A warning "was rejected on …" recalls an earlier decision: quote it to the user with the new proposal.
4. **Report** the pending proposals to the user as a table: id, change, parent or target, pages, reason. `taxonomy pending` lists them.

Done when `taxonomy check` prints `taxonomy: ok` and every proposal the request called for is pending.

## From a run's missing units

`taxonomy hints --out runs/<name>` groups the units classification could not name: pages, the node they fell back to, hosts, examples, and `matches`, the nodes whose label is already that name. For each unit with several pages: a match becomes an `update` (alternative label); otherwise confirm the unit on two or three digests and its home page under `raw/`, then propose an `add`. A unit with one page waits for more warrant.

The other three axes (`publics`, `types_document`, `offre_etudes`) have no hint. Propose a node there when you see a recurring kind of page that every existing id describes badly, with those pages as evidence.

## Deciding: accept and reject

`taxonomy accept ID…` runs only for ids the user named in their own message. It applies them in file order, raises the version (`deprecate` major, `add` and `move` minor, `update` patch) and writes the history. Report the accepted ids, the version line it prints, and that the next `classify` tags every page again. Group the user's decisions into one accept where you can: each accept costs one full reclassification.

`taxonomy reject ID… --reason "…"` removes proposals the user turned down, with their reason in English. The taxonomy does not change.

Each accept or reject is a piece of work: commit the changed taxonomy files and push.

## Exporting

`taxonomy export --format csv|json|md|ttl [--output FILE] [--lang fr]`. Markdown is written in the language of `--lang`; Turtle is SKOS, each axis a concept scheme. Exports are computed from the files: write them where the user asks, outside the repository unless they ask to commit one.

## Reading the record

`taxonomy history [ID]` lists decisions with version, date and reason. `taxonomy show ID` includes the node's history and any pending proposal.

## Changing the gate itself

The code is `src/uzh_crawler/gate/`; the checked rules are `RULES` in `checks.py`. A change to a command, a rule, a kind of change, a field or an export is finished when:

- `docs/taxonomy.md` and this file describe the new behaviour, checked against the code;
- a new rule has a fault in `FAULTS` of `tests/test_gate.py` that shows it, and a row in the rules table of `docs/taxonomy.md`;
- `.venv/bin/python -m pytest` passes, including `tests/test_gate_docs.py`, which fails when a command, rule, format or kind of change is missing from the page or from this skill.
