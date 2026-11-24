"""Crawl speed benchmark: how long does it take to list about ten URLs?

The task is fixed: list the study section of df.uzh.ch one link deep. It reads
the section index and the pages it links to, and writes their URLs to urls.txt.
The benchmark runs the real command against the real site several times, each
time in an empty run directory, and reports the median wall-clock time.

    .venv/bin/python benchmarks/crawl_speed.py                    # measure the current code
    .venv/bin/python benchmarks/crawl_speed.py --label "my idea"  # and record it
    .venv/bin/python benchmarks/crawl_speed.py --profile          # where the time goes

A run only counts when it lists the same URLs as the reference list in
expected_<task>.txt, so a change cannot get faster by finding less.
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / "results.jsonl"

# "links" is the headline task: about ten URLs, found by following links.
# "sitemap" lists a whole site from its sitemaps, the default first step of a crawl.
TASKS = {
    "links": [
        "crawl",
        "www.df.uzh.ch/en/studies.html",
        "--include-prefix", "df.uzh.ch", "/en/studies.html",
        "--include-prefix", "df.uzh.ch", "/en/studies",
        "--no-sitemap",
        "--max-depth", "1",
        "--max-pages", "0",
    ],
    "sitemap": ["crawl", "www.df.uzh.ch", "--sitemap-only"],
}
DEFAULT_COMMAND = [sys.executable, "-m", "uzh_crawler"]


def run_once(command: list[str], task: list[str], extra: list[str]) -> tuple[float, list[str], str]:
    """One crawl in a fresh directory. Returns seconds, the URLs listed, and stderr."""
    with tempfile.TemporaryDirectory(prefix="crawl-bench-") as out:
        started = time.perf_counter()
        done = subprocess.run(
            [*command, *task, *extra, "--out", out],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        seconds = time.perf_counter() - started
        listing = Path(out) / "urls.txt"
        urls = listing.read_text(encoding="utf-8").split() if listing.is_file() else []
    return seconds, urls, done.stderr


def measure(command: list[str], name: str, extra: list[str], runs: int, pause: float) -> dict:
    EXPECTED = HERE / f"expected_{name}.txt"
    task = TASKS[name]
    expected = EXPECTED.read_text(encoding="utf-8").split() if EXPECTED.is_file() else None
    times: list[float] = []
    wrong = 0
    count = 0
    for index in range(runs):
        seconds, urls, stderr = run_once(command, task, extra)
        count = len(urls)
        if expected is None:
            expected = urls
            EXPECTED.write_text("\n".join(urls) + "\n", encoding="utf-8")
            print(f"reference list written: {len(urls)} URLs", file=sys.stderr)
        correct = sorted(urls) == sorted(expected)
        if not correct:
            wrong += 1
            missing = sorted(set(expected) - set(urls))
            extra_urls = sorted(set(urls) - set(expected))
            print(f"  run {index + 1}: WRONG LIST missing={missing[:3]} extra={extra_urls[:3]}", file=sys.stderr)
            if not urls:
                print(stderr[-600:], file=sys.stderr)
        else:
            times.append(seconds)
        print(f"  run {index + 1}: {seconds:.3f}s  {len(urls)} URLs", file=sys.stderr)
        time.sleep(pause)
    if not times:
        return {"runs": runs, "wrong": wrong, "urls": count}
    return {
        "median": round(statistics.median(times), 3),
        "min": round(min(times), 3),
        "max": round(max(times), 3),
        "runs": runs,
        "wrong": wrong,
        "urls": count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task", choices=sorted(TASKS), default="links")
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between runs")
    parser.add_argument("--label", help="record the result in results.jsonl under this name")
    parser.add_argument("--command", help="another program to measure, given the same arguments")
    parser.add_argument("--extra", default="", help="extra crawl options, as one string")
    parser.add_argument("--profile", action="store_true", help="print where one run spends its time")
    args = parser.parse_args()

    command = shlex.split(args.command) if args.command else DEFAULT_COMMAND
    extra = shlex.split(args.extra)
    if args.profile:
        with tempfile.TemporaryDirectory(prefix="crawl-bench-") as out:
            subprocess.run(
                [sys.executable, "-X", "importtime", "-c", "import uzh_crawler.cli"],
                cwd=ROOT, stderr=open(Path(out) / "imports.txt", "w"),
            )
            lines = (Path(out) / "imports.txt").read_text().splitlines()[1:]
            rows = sorted(
                ((int(line.split("|")[1]), line.split("|")[2].strip()) for line in lines),
                reverse=True,
            )
            print("slowest imports (cumulative ms):")
            for micro, name in rows[:12]:
                print(f"  {micro / 1000:7.1f}  {name}")
            stats = Path(out) / "profile.out"
            subprocess.run(
                [sys.executable, "-m", "cProfile", "-o", str(stats), "-m", "uzh_crawler",
                 *TASKS[args.task], *extra, "--out", str(Path(out) / "run")],
                cwd=ROOT, capture_output=True,
            )
            import pstats

            pstats.Stats(str(stats)).sort_stats("cumulative").print_stats(28)
        return 0

    result = measure(command, args.task, extra, args.runs, args.pause)
    print(json.dumps(result))
    if args.label and "median" in result:
        with RESULTS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"label": args.label, "task": args.task, **result}, ensure_ascii=False) + "\n")
    return 0 if result.get("wrong") == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
