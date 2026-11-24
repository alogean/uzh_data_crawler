# Command line

The entry point is `python -m uzh_crawler` or `uzh-crawler`. Installation and the Ollama setup are in the [README](../README.md). Every command works on one run directory, given with `--out`.

## Commands

| Command | Role | Needs |
| --- | --- | --- |
| `crawl [DOMAIN…]` | Lists every URL of a domain or section | Network |
| `scrape [URL…]` | Downloads URLs, stores the original files, extracts their text | Network |
| `summarize [URL…]` | Summarizes the scraped documents | Ollama |
| `classify [URL…]` | Tags the summaries with the taxonomy | Ollama, `--taxonomy` |
| `run [DOMAIN…]` | Chains the four stages above | All of them |
| `validate` | Checks `crawl.json`, the summaries and the digests | `--taxonomy` |
| `proposals hints` | Lists the units that classification could not name | A classified run |
| `proposals check` | Verifies `taxonomy.proposals.json` | `--taxonomy` |
| `proposals accept ID…` | Moves proposals into `taxonomy.json` | `--taxonomy` |

```bash
python -m uzh_crawler crawl phil.uzh.ch --out runs/phil --max-pages 0
python -m uzh_crawler scrape --out runs/phil
python -m uzh_crawler summarize --out runs/phil --lang en
python -m uzh_crawler classify --out runs/phil --taxonomy taxonomy.json
```

`scrape`, `summarize` and `classify` without URLs work on every page of the run that is ready for that stage and not already done. With URLs they work on those only, and redo them even if a result exists. `scrape URL…` can also start a run directory of its own, without a crawl. `summarize` and `classify` accept only URLs that are already in the run.

`--verbose` on any command prints debug logs. It goes after the command name.

## crawl

`crawl` needs at least one start point: a `DOMAIN` argument, `--seed`, or `--study-sections`. A `DOMAIN` is a host (`phil.uzh.ch`), a host with a path (`www.oec.uzh.ch/de/studies.html`), or a full URL. It must be under `uzh.ch`.

| Option | Default | Effect |
| --- | --- | --- |
| `--out` | required | Run directory. Its name becomes `run_id` |
| `--seed URL` | | Another way to give a start URL. Can be repeated |
| `--study-sections` | off | Adds the eight study and faculty sections and their prefixes. See [Scope](scope.md) |
| `--max-pages` | `500` | Maximum number of HTML pages read. `0` removes the ceiling |
| `--max-depth` | no limit | Depth of the links followed. Pages at that depth are listed without being read, since their links would not be followed. `0` lists the start URLs and the sitemap URLs and reads nothing |
| `--same-host` | yes | Stays on the start hosts. `www` and the bare host are the same site. This is the default; the flag only states it |
| `--allow-subdomains` | no | Follows any `*.uzh.ch` host. Cannot be combined with `--same-host` |
| `--include-prefix HOST PREFIX` | | Confines a host to a path. Repeat the option |
| `--exclude-path TEXT` | | Skips URLs whose path contains this text. Repeat the option |
| `--no-sitemap` | | Does not read sitemaps; the list comes from links only |
| `--sitemap-only` | | Lists the sitemap URLs and reads no page. The fastest mode |
| `--rate-limit` | `3` | Steady rate, in requests per second and per host. `0` does not wait |
| `--burst` | `20` | Requests a host receives at once before the steady rate applies |
| `--crawl-concurrency` | `20` | Parallel requests |
| `--ignore-robots` | off | Ignores `robots.txt` |

Sitemaps are read by default. The three ways to build the list are compared in [Scope](scope.md#how-the-list-is-built). How fast a crawl is, and why, is in [Crawl performance](crawl-performance.md).

## scrape

| Option | Default | Effect |
| --- | --- | --- |
| `--out` | required | Run directory |
| `--refresh` | off | Also asks again for pages already stored, with a conditional request |
| `--js-fallback` | off | One Playwright pass when the useful text is under 200 characters |
| `--max-pages` | `0` | Maximum number of downloads in this execution. `0` means no ceiling |
| `--rate-limit`, `--burst`, `--crawl-concurrency`, `--ignore-robots` | as for `crawl` | |

The scope of the run is the one recorded by `crawl`; `scrape` has no scope options.

The HTTP client identifies itself as `askUZH-TaxonomyCrawler/0.1 (+local; research)`, follows redirects, and uses a 60-second timeout. `HTTP_PROXY` and `HTTPS_PROXY` apply to `crawl` and `scrape`. Calls to Ollama ignore them.

## summarize and classify

| Option | Default | |
| --- | --- | --- |
| `--out` | required | Run directory |
| `--taxonomy` | `taxonomy.json` | `classify` only |
| `--model` | `qwen3.5:9b` | The model for this stage. The two stages can use different models |
| `--ollama-url` | `http://localhost:11434` | |
| `--llm-concurrency` | `2` | Parallel model calls |
| `--lang` | `fr` | `fr`, `de` or `en`. Language of the summary, and of the labels shown to the classification model |
| `--num-ctx` | `32768` | |
| `--temperature` | `0.1` | |

## run

`run` accepts the options of all four stages and applies `--model` to both model stages. It is the same as running `crawl`, `scrape`, `summarize` and `classify` in turn on one directory, with one difference: its `--max-pages` (500 by default) caps the pages read by the crawl and, separately, the downloads of the scrape. Pass `--max-pages 0` for a complete run.

## proposals

```bash
python -m uzh_crawler proposals hints --out runs/phil
python -m uzh_crawler proposals check --taxonomy taxonomy.json
python -m uzh_crawler proposals accept philosophisches_seminar --taxonomy taxonomy.json
```

`hints` prints JSON on standard output. `check` and `accept` read `taxonomy.proposals.json` in the current directory, or the file given with `--proposals`. The workflow is described in [Taxon and its skills](agent.md#taxonomy-proposals).

## Progress output

Progress is written to standard error, one line at a time, with the elapsed time and an estimate of the time left. Standard output carries only `validate: ok`, `proposals: ok`, the `accepted:` lines and the JSON of `proposals hints`.

```text
[crawl] 1 URL de départ · profondeur libre · sans plafond · écoulé 0s
[crawl 12/340] · lu · https://www.phil.uzh.ch/de/studium.html · écoulé 14s · reste ~6min 12s
[crawl] 512 URL dans urls.txt · écoulé 3min 02s
[scrape 40/512] · fetched · https://www.phil.uzh.ch/dam/jcr:…/Wegleitung.pdf · écoulé 21s · reste ~4min 08s
[summarize 3/120] · résumé · https://www.phil.uzh.ch/de/studium.html · écoulé 1min 02s · reste ~40min 18s
[classify 3/120] · tags · https://www.phil.uzh.ch/de/studium.html · écoulé 9s · reste ~5min 51s
```

The option help (`--help`) and these progress messages are in French. The words that appear in a progress line:

| Text | Meaning |
| --- | --- |
| `écoulé`, `reste` | Elapsed time, estimated time left |
| `URL de départ`, `profondeur`, `plafond` | Start URLs, depth, page ceiling (`libre` and `sans plafond` mean no limit) |
| `lu` | crawl: the HTML page was read for its links |
| `listé` | crawl: the URL was added to the list without a download |
| `copie locale` | scrape: the HTML kept by the crawl was used, with no new request |
| `cache HTTP` | The server answered 304; the stored copy is kept |
| `fetched`, `empty`, `failed`, `blocked_auth` | The page status just written. See [Outputs](outputs.md) |
| `hors périmètre` | The host is out of scope |
| `hors section` | Same site, but outside the prefixes or excluded, or a redirect left the section |
| `robots` | Refused by `robots.txt` |
| `déjà classé` | Already `blocked_auth` or `out_of_scope`; not requested again |
| `lecture des sitemaps` | Reading sitemaps |
| `modèle en cours` | The model is working on this document |
| `résumé`, `vide`, `erreur résumé` | Summary written, document too short, summary failed |
| `tags`, `erreur tags` | Tags written, classification failed |
| `rien à recalculer … modèle non rappelé` | Every result is current; the model was not called |

The last line of a command gives the counters: `<run_id>: discovered=… fetched_ok=… failed=… summarized=… tagged=…`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Finished, no page in `failed`, `summary_error` or `tag_error`. For `validate` and `proposals check`: nothing to report |
| `1` | Invalid configuration, a run directory that does not match the command, a URL that is not in the run, or Ollama unreachable |
| `2` | The command finished, but at least one page of the run is `failed`, `summary_error` or `tag_error`. For `validate` and `proposals check`: at least one problem |

The exit code reflects the whole run directory, not only the pages this command touched. A URL refused by `robots.txt` is `failed` with the error `disallowed by robots.txt` and does not cause code 2 on its own.

An argparse usage error also exits with code 1, so it is not mistaken for a partial failure.
