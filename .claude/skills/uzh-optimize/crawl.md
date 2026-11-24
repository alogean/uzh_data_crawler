# Crawl: what was tried

State after twenty iterations, measured with `benchmarks/crawl_speed.py` against the real `df.uzh.ch`. Every measurement is in `docs/crawl-performance.md`; add a row there for each new idea.

| Measure | Before | After |
| --- | --- | --- |
| 13 URLs by links, one level deep (the headline task) | 4.64 s | 0.24 s |
| 979 URLs from sitemaps (`--task sitemap`) | 1.57 s | 0.32 s |
| 98 URLs, 60 pages read, sitemaps and links | 34.8 s | 14.9 s |

The third line is the honest one for real work: a large crawl is paced by the politeness rate, and only the first burst and the requests no longer wasted make it faster.

## What is in the code, and why

| Choice | Gain | Where |
| --- | --- | --- |
| Rate limiter as a token bucket: 20 requests at once per host, then the steady rate | 4.64 s → 0.575 s | `crawl/limiter.py` |
| Links extracted with the lexbor parser (`selectolax`) instead of BeautifulSoup | 13 pages: 127 ms → 8.6 ms; 0.575 s → 0.404 s | `crawl/links.py` |
| Each stage imported only when it runs | Imports: 131 ms → 54 ms; 0.404 s → 0.289 s | `pipeline.py` |
| A page at the depth limit is listed without being requested | 0.289 s → 0.225 s | `crawl/discover.py` |
| Sitemaps fetched in parallel waves | Sitemap task: 1.57 s → 1.52 s | `crawl/sitemap.py` |
| `robots.txt` fetched once per host and shared with sitemap discovery | Sitemap task: 1.52 s → 0.32 s | `crawl/robots.py` |
| `crawl.json` encoded with `orjson` | 51,200 pages: 305 ms → 24 ms per save, identical bytes | `store/crawl_json.py` |

## What did not work

Do not retry these as they are. The last column is the explanation that fits the numbers; it was not itself measured.

| Idea | Result | Likely reason |
| --- | --- | --- |
| Links with the standard library `HTMLParser` | 41 ms against 8.6 ms | Pure Python |
| Links with `lxml` | 17 ms | Twice slower than lexbor, heavier to install |
| Links with regular expressions | 8.7 ms, same as lexbor | No gain, and not an HTML parser: it would break on unusual markup |
| `uvloop` event loop | 0.325 s against 0.294 s | The loop is not where the time goes |
| HTTP/1.1 instead of HTTP/2 | 0.310 s against 0.294 s | One connection per parallel request instead of one for all |
| A libcurl-based client instead of `httpx` | 0.279 s against 0.273 s | Same requests, slower import |
| Brotli or Zstandard | Nothing to measure | The UZH servers only answer with gzip |
| Not storing the HTML read by the crawl | 0.293 s against 0.294 s | No gain, and `scrape` would download every page again |
| Caching URL normalization | 0.296 s against 0.294 s | It was never a cost |
| Python isolated mode (`-I`) | 0.220 s against 0.230 s | Within the noise |
| Console script instead of `python -m` | 0.235 s against 0.230 s | Within the noise |
| A rewrite in Go (`benchmarks/experiments/go-crawl/`) | 0.199 s against 0.308 s for Python in the same session | It only saves the interpreter start-up, about 0.1 s per command, and would need the scope rules, sitemaps, `robots.txt`, manifest and resuming written twice |

## Traps

- **The first optimisation was a bug in disguise.** The old limiter spaced every request, the first ones included. Profile before assuming the network is the cost.
- **Parallel workers can waste the burst.** Twenty workers starting on one host each downloaded `robots.txt`, used all 20 tokens, and the real pages then waited for the rate limit. Whatever is fetched once per host must be fetched by one worker while the others wait for its answer.
- **The site and the network change from day to day.** A median from another session is not a baseline. Noise is about 0.02 s; run at least 7 times.
- **The benchmark is live.** It sends real requests to `df.uzh.ch`, obeys `robots.txt` and identifies itself. Keep the pause between runs, and do not loop it for long.
- **The depth rule moved a check to the scrape.** A page at the depth limit is listed without a request, so a dead link is only discovered when `scrape` downloads it.

## What limits it now

- Three round trips that must happen in sequence: connect, `robots.txt`, the first page. About 0.1 s.
- Python start-up and the HTTP library, about 0.1 s per command.
- Politeness: past the burst, 3 requests per second per host. One third of a second per HTML page read, whatever the code does. Leave the defaults.

## Not tried

Open, not rejected, and without a measurement: keeping the content of `robots.txt` and of sitemaps between two commands on the same run directory, so a second `crawl` does not ask for them again.
