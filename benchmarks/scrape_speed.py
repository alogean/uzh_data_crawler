"""Scrape speed benchmark: how long does it take to turn 55 URLs into Markdown?

The list is fixed: scrape_urls.txt holds 24 HTML pages, 14 PDF files, 10 Word
files and 7 Excel files of UZH sites. Two tasks measure the real command:

    convert   the 55 files are already on disk; only reading them and writing
              Markdown is timed. No network, so the number is stable. Headline.
    live      the 55 URLs are downloaded from the real sites and converted.

    .venv/bin/python benchmarks/scrape_speed.py                    # convert, 7 runs, median
    .venv/bin/python benchmarks/scrape_speed.py --task live
    .venv/bin/python benchmarks/scrape_speed.py --label "my idea"  # and record it
    .venv/bin/python benchmarks/scrape_speed.py --profile          # where the time goes
    .venv/bin/python benchmarks/scrape_speed.py --by-kind          # in-process time per format

A run only counts when every URL ends `fetched` and its Markdown holds the same
words as the reference in expected_scrape/, so a change cannot get faster by
reading less. The files are downloaded once into .cache/ (ignored by git).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
URLS = HERE / "scrape_urls.txt"
EXPECTED = HERE / "expected_scrape"
CORPUS = HERE / ".cache" / "scrape-corpus"
RESULTS = HERE / "results.jsonl"
DEFAULT_COMMAND = [sys.executable, "-m", "uzh_crawler"]

# Share of the reference words that must be found, and share of new words allowed.
MIN_RECALL = 0.97
MAX_EXTRA = 0.05
_WORD = re.compile(r"[^\W_]{2,}")
_LINK_TARGET = re.compile(r"\]\([^)\s]*\)")


def words(markdown: str) -> collections.Counter:
    """The words of a document, without Markdown link targets."""
    return collections.Counter(_WORD.findall(_LINK_TARGET.sub("]", markdown).lower()))


def stem(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def read_urls() -> list[str]:
    return URLS.read_text(encoding="utf-8").split()


def build_corpus() -> None:
    """Download the files once, with the scrape command itself, and keep them as a crawled run."""
    if (CORPUS / "crawl.json").is_file():
        return
    print("downloading the benchmark files once …", file=sys.stderr)
    shutil.rmtree(CORPUS, ignore_errors=True)
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [*DEFAULT_COMMAND, "scrape", *read_urls(), "--out", str(CORPUS)],
        cwd=ROOT, capture_output=True, check=False,
    )
    manifest = json.loads((CORPUS / "crawl.json").read_text(encoding="utf-8"))
    missing = [page["url"] for page in manifest["pages"] if not page.get("file_path")]
    if missing:
        shutil.rmtree(CORPUS)
        raise SystemExit(f"could not download: {missing}")
    # Back to the state a crawl leaves: listed, file on disk, nothing extracted.
    for page in manifest["pages"]:
        for key in ("raw_path", "content_hash", "title", "error", "fetched_at"):
            page[key] = None
        page["status"] = "discovered"
    (CORPUS / "crawl.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(CORPUS / "raw", ignore_errors=True)


def check(out: Path) -> list[str]:
    """Problems of one finished run, compared with the reference texts."""
    problems: list[str] = []
    try:
        manifest = json.loads((out / "crawl.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"crawl.json unreadable: {exc}"]
    pages = {page["url"]: page for page in manifest["pages"]}
    EXPECTED.mkdir(exist_ok=True)
    for url in read_urls():
        page = pages.get(url)
        if page is None or page.get("status") != "fetched" or not page.get("raw_path"):
            problems.append(f"not fetched: {url} ({page and page.get('status')}: {page and page.get('error')})")
            continue
        text = (out / page["raw_path"]).read_text(encoding="utf-8")
        reference = EXPECTED / f"{stem(url)}.md"
        if not reference.is_file():
            reference.write_text(text, encoding="utf-8")
            print(f"reference written: {reference.name}", file=sys.stderr)
            continue
        want, got = words(reference.read_text(encoding="utf-8")), words(text)
        total = sum(want.values()) or 1
        recall = sum((want & got).values()) / total
        extra = sum((got - want).values()) / total
        if recall < MIN_RECALL or extra > MAX_EXTRA:
            lost = [word for word, _ in (want - got).most_common(5)]
            added = [word for word, _ in (got - want).most_common(5)]
            problems.append(f"text differs: {url} recall={recall:.3f} extra={extra:.3f} lost={lost} added={added}")
    return problems


def multiply(out: Path, copies: int) -> None:
    """Make the run ``copies`` times larger: the same files under more URLs."""
    path = out / "crawl.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    originals = list(manifest["pages"])
    for copy in range(1, copies):
        for page in originals:
            url = f"{page['url']}?copy={copy}"
            manifest["pages"].append({**page, "url": url, "normalized_url": url})
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def run_once(command: list[str], task: str, extra: list[str], copies: int = 1) -> tuple[float, list[str], str]:
    with tempfile.TemporaryDirectory(prefix="scrape-bench-") as temporary:
        out = Path(temporary) / "run"
        if task == "convert":
            shutil.copytree(CORPUS, out)
            if copies > 1:
                multiply(out, copies)
            arguments = ["scrape", "--ignore-robots", *extra, "--out", str(out)]
        else:
            arguments = ["scrape", *read_urls(), *extra, "--out", str(out)]
        started = time.perf_counter()
        done = subprocess.run([*command, *arguments], cwd=ROOT, capture_output=True, text=True)
        seconds = time.perf_counter() - started
        return seconds, check(out), done.stderr


def measure(command: list[str], task: str, extra: list[str], runs: int, pause: float, copies: int = 1) -> dict:
    times: list[float] = []
    wrong = 0
    for index in range(runs):
        seconds, problems, stderr = run_once(command, task, extra, copies)
        if problems:
            wrong += 1
            print(f"  run {index + 1}: WRONG ({len(problems)} problems)", file=sys.stderr)
            for problem in problems[:8]:
                print(f"    {problem}", file=sys.stderr)
            if len(problems) > 20:
                print(stderr[-800:], file=sys.stderr)
        else:
            times.append(seconds)
        print(f"  run {index + 1}: {seconds:.3f}s", file=sys.stderr)
        if task == "live":
            time.sleep(pause)
    result = {"runs": runs, "wrong": wrong, "urls": len(read_urls()) * copies}
    if times:
        result = {
            "median": round(statistics.median(times), 3),
            "min": round(min(times), 3),
            "max": round(max(times), 3),
            **result,
        }
    return result


def by_kind() -> None:
    """In-process time of each extractor over the corpus, best of three."""
    sys.path.insert(0, str(ROOT / "src"))
    from uzh_crawler.scrape import extract
    from uzh_crawler.scrape.fetch import decode_text

    manifest = json.loads((CORPUS / "crawl.json").read_text(encoding="utf-8"))
    readers = {
        ".html": lambda data, page: extract.extract_html(decode_text(data, page["content_type"]), page["url"]),
        ".pdf": lambda data, page: extract.extract_pdf(data),
        ".docx": lambda data, page: extract.extract_docx(data),
        ".xlsx": lambda data, page: extract.extract_xlsx(data),
    }
    totals: dict[str, list[float]] = collections.defaultdict(list)
    for _ in range(3):
        spent: dict[str, float] = collections.defaultdict(float)
        for page in manifest["pages"]:
            suffix = Path(page["file_path"]).suffix
            data = (CORPUS / page["file_path"]).read_bytes()
            started = time.perf_counter()
            readers[suffix](data, page)
            spent[suffix] += time.perf_counter() - started
        for suffix, seconds in spent.items():
            totals[suffix].append(seconds)
    count = collections.Counter(Path(page["file_path"]).suffix for page in manifest["pages"])
    for suffix, seconds in sorted(totals.items(), key=lambda item: -min(item[1])):
        print(f"  {suffix:6} {count[suffix]:3} files  {min(seconds) * 1000:8.1f} ms")
    print(f"  total             {sum(min(s) for s in totals.values()) * 1000:8.1f} ms")


def profile(extra: list[str]) -> None:
    import pstats

    with tempfile.TemporaryDirectory(prefix="scrape-bench-") as temporary:
        out = Path(temporary) / "run"
        shutil.copytree(CORPUS, out)
        stats = Path(temporary) / "profile.out"
        subprocess.run(
            [sys.executable, "-m", "cProfile", "-o", str(stats), "-m", "uzh_crawler",
             "scrape", "--ignore-robots", *extra, "--out", str(out)],
            cwd=ROOT, capture_output=True,
        )
        pstats.Stats(str(stats)).sort_stats("cumulative").print_stats(40)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--task", choices=("convert", "live"), default="convert")
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between live runs")
    parser.add_argument("--label", help="record the result in results.jsonl under this name")
    parser.add_argument("--command", help="another program to measure, given the same arguments")
    parser.add_argument("--extra", default="", help="extra scrape options, as one string")
    parser.add_argument("--copies", type=int, default=1, help="convert task: repeat the files this many times")
    parser.add_argument("--profile", action="store_true", help="print where one convert run spends its time")
    parser.add_argument("--by-kind", action="store_true", help="print the extraction time of each format")
    args = parser.parse_args()

    build_corpus()
    extra = shlex.split(args.extra)
    if args.by_kind:
        by_kind()
        return 0
    if args.profile:
        profile(extra)
        return 0
    command = shlex.split(args.command) if args.command else DEFAULT_COMMAND
    result = measure(command, args.task, extra, args.runs, args.pause, args.copies)
    print(json.dumps(result))
    if args.label and "median" in result:
        with RESULTS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"label": args.label, "task": f"scrape-{args.task}", **result}, ensure_ascii=False) + "\n")
    return 0 if result.get("wrong") == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
