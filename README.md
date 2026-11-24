# uzh_data_crawler

**Taxon** is an agent that maintains a taxonomy of the web of the University of Zurich. It surveys `uzh.ch` sites, tags their documents with the closed vocabulary in [`taxonomy.json`](taxonomy.json), and turns what does not fit into proposals for a person to accept.

Taxon is [Claude Code](https://claude.com/claude-code) opened in this repository: [`CLAUDE.md`](CLAUDE.md) gives it its name and its mission, and it has four skills. Each skill drives one command of the `uzh_crawler` Python package, which does the actual work and can be used on its own. Summaries and tags come from a model running on your machine through [Ollama](https://ollama.com/).

| Skill | Command | What it does |
| --- | --- | --- |
| `uzh-crawl` | `crawl` | Lists every URL of a domain or section, without downloading the documents |
| `uzh-scrape` | `scrape` | Downloads URLs, stores the original files and extracts their text |
| `uzh-summarize` | `summarize` | Summarizes each document with the local model |
| `uzh-classify` | `classify` | Tags each summary with ids from the taxonomy |

The repository also holds the askUZH taxonomy browser in [`browser/`](browser/), a static page that displays `taxonomy.json`.

## Documentation

| Page | Content |
| --- | --- |
| [Taxon and its skills](docs/agent.md) | What Taxon does, how to call it, how taxonomy proposals work |
| [How a run works](docs/README.md#how-a-run-works) | Workflow diagram of the four stages |
| [Command line](docs/cli.md) | Commands, options, progress output, exit codes |
| [Scope](docs/scope.md) | Hosts, path prefixes, sitemaps, exclusions, what is downloaded and extracted |
| [Resume and cache](docs/resume-and-cache.md) | Resuming a run, HTTP cache, when a model call is skipped |
| [Outputs](docs/outputs.md) | `crawl.json`, `urls.txt`, `files/`, `raw/`, `summaries/`, `digests/`, statuses, `validate` |
| [Tags](docs/tags.md) | Taxonomy contract, the two model calls, tag validation |
| [Code layout](docs/README.md#code-layout) | Which module does what |
| [Original specification](docs/SPEC-crawl-summary-tags-pipeline.md) | The v1 brief the tool was first built from |

## Installation

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The entry point is `python -m uzh_crawler` or `uzh-crawler`.

The JavaScript fallback (`scrape --js-fallback`) is optional:

```bash
pip install -e ".[js]"
playwright install chromium
```

## Ollama

`summarize` and `classify` need Ollama answering on `http://localhost:11434`. `crawl`, `scrape` and `validate` do not.

The intended model on a 48 GB Mac is a Qwen 3 of about 9B parameters in 4-bit quantization, `qwen3.5:9b` by default. If that tag is not published in your Ollama, pass an equivalent Qwen 3.x model with `--model`.

```bash
ollama pull qwen3.5:9b
```

`--llm-concurrency` defaults to 2. If you raise it, set `OLLAMA_NUM_PARALLEL` on the server to match.

## Quick start

Open Claude Code in this repository. [`CLAUDE.md`](CLAUDE.md) is loaded at the start of the session and makes it Taxon, so you can ask directly:

```text
Survey phil.uzh.ch and tell me which units have no node in the taxonomy.
```

By hand, the same work is four commands on one run directory:

```bash
python -m uzh_crawler crawl phil.uzh.ch --out runs/phil --max-pages 0
python -m uzh_crawler scrape --out runs/phil
python -m uzh_crawler summarize --out runs/phil --lang en
python -m uzh_crawler classify --out runs/phil --taxonomy taxonomy.json
python -m uzh_crawler validate --out runs/phil --taxonomy taxonomy.json
```

`run` chains the first four. Each command can also take URLs to work on a few documents only, and running a command again resumes where it stopped.

## Tests

```bash
pytest
```

The tests call neither the UZH site nor Ollama. They use a simulated HTTP client and fake models.
