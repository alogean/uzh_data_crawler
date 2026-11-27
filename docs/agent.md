# Taxon and its skills

Opening this repository in Claude Code gives you **Taxon**, the agent of this project: [`CLAUDE.md`](../CLAUDE.md) at the root is loaded at the start of every session and tells Claude its name and its mission, which is to keep [`taxonomy.json`](../taxonomy.json) true to the UZH web it describes. Taxon has five skills. Four are the instructions for one stage each, and each stage is one command of the `uzh_crawler` package. The fifth, `uzh-optimize`, is about the code itself: how to make a stage faster.

| File | Role |
| --- | --- |
| [`CLAUDE.md`](../CLAUDE.md) | Taxon: name, mission, how the stages fit together, how to propose taxonomy changes. Loaded automatically |
| [`AGENTS.md`](../AGENTS.md) | The same instructions for Codex: it tells Codex to load and follow `CLAUDE.md`, which wins on any conflict |
| [`.claude/skills/uzh-crawl/SKILL.md`](../.claude/skills/uzh-crawl/SKILL.md) | Listing the URLs of a domain |
| [`.claude/skills/uzh-scrape/SKILL.md`](../.claude/skills/uzh-scrape/SKILL.md) | Downloading and storing documents |
| [`.claude/skills/uzh-summarize/SKILL.md`](../.claude/skills/uzh-summarize/SKILL.md) | Summarizing with the local model |
| [`.claude/skills/uzh-classify/SKILL.md`](../.claude/skills/uzh-classify/SKILL.md) | Tagging summaries with the taxonomy |
| [`.claude/skills/uzh-optimize/`](../.claude/skills/uzh-optimize/SKILL.md) | Making a stage faster: the method, and what was already tried for `crawl`, `scrape` and `summarize` |
| [`.claude/agents/taxon.md`](../.claude/agents/taxon.md) | Taxon as a subagent, for a task delegated to the background. It follows `CLAUDE.md` |

## Who does what

Three actors are involved, and the split between them is deliberate.

- **The Python package** does everything that must be exact and repeatable: HTTP, scope rules, file storage, hashes, caches, the closed JSON schema, validation.
- **The local model** (Ollama) reads documents. It writes each summary, and it picks tags from the list of ids it is given. It never sees the taxonomy file itself and cannot store a tag outside it.
- **Taxon** (Claude) decides what to run and in which order, reads the results, reports, and writes taxonomy proposals. It never edits `taxonomy.json` by hand.

## Two standing duties

Whatever it is asked to do, Taxon keeps two things true. Both are rules in [`CLAUDE.md`](../CLAUDE.md).

**The documentation describes Taxon as it is.** Whenever Taxon changes how the project behaves, it updates the pages and diagrams that describe that behaviour in the same piece of work, and keeps them simple, built on examples, and visual.

**Taxon's state is on GitHub.** The state is everything git tracks: `CLAUDE.md`, the skills, the code, the tests, the documentation and the taxonomy. Crawl data under `runs/` is not part of it.

The first thing Taxon does in a session is compare the local copy with GitHub and align them. The diagram reads from top to bottom: one check, then one action per situation.

```mermaid
flowchart TD
    start["Session opens"] --> fetch["git fetch origin<br/>git status -sb"]
    fetch --> state{"Local copy<br/>compared with GitHub"}
    state -- "identical" --> ready["Go on with the request"]
    state -- "GitHub is ahead" --> pull["git pull --ff-only"]
    state -- "local is ahead" --> push["git push"]
    state -- "both moved" --> rebase["git pull --rebase<br/>then git push"]
    state -- "uncommitted changes" --> commit["Run the tests,<br/>commit, push"]
    pull --> ready
    push --> ready
    rebase --> ready
    commit --> ready
    rebase -. "conflict" .-> ask["Stop and ask"]
    commit -. "tests fail" .-> ask
```

During the session, each finished piece of work ends the same way: tests pass, documentation updated, commit, push. Taxon stops and asks instead of pushing when a pull hits a conflict, when tests fail on changes it did not make, or when GitHub refuses the push. It never rewrites history on GitHub.

## Using Taxon

Open Claude Code in the repository and ask. There is nothing to select: the session is Taxon.

```text
List all the URLs of oec.uzh.ch.
Survey phil.uzh.ch end to end.
Which units of runs/phil have no node in the taxonomy?
Scrape https://www.phil.uzh.ch/de/studium.html and summarize it in English.
```

For a long survey that should not occupy the session, ask for it to be delegated to the `taxon` subagent.

## The four stage skills

| Skill | Input | Output | Needs |
| --- | --- | --- | --- |
| `uzh-crawl` | A domain or a section URL | `urls.txt`, the list of all URLs, and `crawl.json` | Network |
| `uzh-scrape` | URLs, or the list of a crawl | `files/` (originals) and `raw/` (Markdown text) | Network |
| `uzh-summarize` | Scraped documents | `summaries/` | Ollama |
| `uzh-classify` | Summaries and `taxonomy.json` | `digests/` (summary plus tags) | Ollama |

Each stage only needs the output of the one before it, so they can be run on different days, on a subset of URLs, or redone alone. Changing the taxonomy, for instance, only redoes `classify`.

Classification reads the summary, not the document. The summary stage is therefore prompted to keep what classification needs: the publishing unit, the audience, the kind of document and the study level. A summary that omits one of these gives a weaker tag on that axis; this is the price of keeping the two stages separate.

## The optimization skill

`uzh-optimize` is not a stage. It is what Taxon loads when the request is "make this faster", and it serves as a memory: `crawl` and `scrape` were each tuned in twenty measured attempts, `summarize` in fifty, and most attempts failed. Without a record, the next session would try the same dead ends again.

A skill is a folder, and Claude reads its files only when it needs them. This skill uses that to separate what is general from what is specific:

| File | Holds | Read |
| --- | --- | --- |
| [`SKILL.md`](../.claude/skills/uzh-optimize/SKILL.md) | The method, and the lessons that held for both stages | Every time the skill is loaded |
| [`crawl.md`](../.claude/skills/uzh-optimize/crawl.md) | For `crawl`: what is in the code and why, what failed, the traps, what limits the speed now | Only before work on the crawl |
| [`scrape.md`](../.claude/skills/uzh-optimize/scrape.md) | The same for `scrape` | Only before work on the scrape |

A stage skill such as `uzh-scrape` says how to *run* the stage and stays short. It points to `uzh-optimize` in one sentence, so a session that only scrapes a site never loads the history of the optimization.

The method is a loop. Read the diagram from the top: one idea goes round once, and it is written down whether it is kept or not.

```mermaid
flowchart TD
    read["Read the stage file:<br/>what was already tried"] --> base["Measure today's median<br/>with the benchmark"]
    base --> profile["Find where the time goes"]
    profile --> change["Make one change"]
    change --> test["Run the tests,<br/>then the benchmark"]
    test --> guard{"Same result<br/>as the reference?"}
    guard -- "no" --> revert["Revert the change"]
    guard -- "yes" --> faster{"Faster by more<br/>than the noise?"}
    faster -- "no" --> revert
    faster -- "yes" --> keep["Keep the change"]
    keep --> record["Record it: a row in the performance page,<br/>a lesson in the stage file"]
    revert --> record
    record --> profile
```

The two records have different readers. The pages [Crawl performance](crawl-performance.md) and [Scrape performance](scrape-performance.md) tell the whole story to a person, with every measurement. The stage files of the skill are the short version Taxon acts on: do this, do not retry that, watch out for this.

For example, asking "can the scrape read PDF files faster?" makes Taxon open `scrape.md`, where it finds that three other PDF readers were already measured and lost, and why, before it writes any code.

## Taxonomy proposals

The taxonomy skeleton has no node for most institutes, departments and services. When the classification model has to fall back to a broader `organisation` node, it also writes the unit's name, as free text, in the page's `missing_unit` field. That field is the bridge from a crawl to the taxonomy.

```mermaid
flowchart LR
    classify["classify<br/>records missing_unit"] --> hints["proposals hints<br/>groups the unit names"]
    hints --> agent["Taxon checks the unit<br/>and writes a candidate node"]
    agent --> file[("taxonomy.proposals.json")]
    file --> check["proposals check"]
    check --> person{"A person<br/>accepts?"}
    person -- "yes" --> accept["proposals accept ID"]
    accept --> taxonomy[("taxonomy.json")]
    person -- "no" --> file
```

1. `proposals hints --out runs/phil` prints, as JSON, each unit name with its number of pages, the node those pages were tagged with, their hosts and example URLs.
2. Taxon confirms that a name is a real unit, finds its parent, and writes a candidate node to `taxonomy.proposals.json` with trilingual labels and definitions, the unit's home page as source, and the reason.
3. `proposals check` verifies the file: known axis, new id, parent in the same axis, labels and definitions in German, French and English, at least one source, a reason.
4. You read the proposals and choose. `proposals accept ID…` moves the chosen nodes into `taxonomy.json` with the status `accepted` and removes them from the proposals file.

Taxon runs `accept` only for ids you have named. Accepting a node changes the taxonomy fingerprint, so the next `classify` tags every page again; the summaries are reused.

A proposal has this shape:

```json
{
  "schema_version": 1,
  "kind": "taxonomy_proposals",
  "proposals": [
    {
      "axis": "organisation",
      "node": {
        "id": "philosophisches_seminar",
        "parent": "philosophische",
        "labels": {"de": "Philosophisches Seminar", "fr": "Séminaire de philosophie", "en": "Department of Philosophy"},
        "definition": {"de": "…", "fr": "…", "en": "…"},
        "status": "proposed",
        "sources": ["https://www.philosophie.uzh.ch/"]
      },
      "reason": "34 pages of runs/phil fell back to philosophische",
      "evidence": ["https://www.philosophie.uzh.ch/de/studium.html"]
    }
  ]
}
```
