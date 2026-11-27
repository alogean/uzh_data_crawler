"""Summarize speed benchmark: how long does it take to summarize 13 UZH documents?

The documents are fixed: summarize_corpus/ holds the Markdown of 13 very
different pages and files (691 to 459,000 characters, German and English).
summarize_reference/ holds, for each of them, the summary written by a large
model (Claude Fable) and the checklist of facts a good summary must contain.

    .venv/bin/python benchmarks/summarize_speed.py                      # 3 runs, median
    .venv/bin/python benchmarks/summarize_speed.py --label "my idea"    # and record it
    .venv/bin/python benchmarks/summarize_speed.py -- --model qwen3.5:4b --llm-concurrency 4
    .venv/bin/python benchmarks/summarize_speed.py --score-reference    # check the checklist itself

The time is the wall-clock time of the real `summarize` command, once the model
is loaded: before each run the model is unloaded, which empties Ollama's prompt
cache, then a short warm-up document is summarized and not timed.

A run only counts when it passes the quality guard: every document summarized,
in French, with enough of the checklist facts and no more invented numbers than
the limit. A change cannot get faster by saying less or by making things up.
The summaries of the last run are kept in .cache/summarize-last/ to be read.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORPUS = HERE / "summarize_corpus"
REFERENCE = HERE / "summarize_reference"
RESULTS = HERE / "results.jsonl"
LAST = HERE / ".cache" / "summarize-last"
DEFAULT_COMMAND = [sys.executable, "-m", "uzh_crawler"]
DEFAULT_MODEL = "gemma4:e2b"  # the default of the command

# The quality guard. See docs/summarize-performance.md for how they were chosen.
MIN_COVERAGE = 0.70  # mean weighted share of the checklist facts found
MIN_DOC_COVERAGE = 0.30  # the same for the worst document
MAX_UNSUPPORTED = 4  # numbers written in the summaries and absent from the documents

_FRENCH = {"le", "la", "les", "des", "est", "une", "pour", "et", "dans", "sur", "du", "aux", "avec", "par"}
_NUMBER = re.compile(r"\d+")


def fold(text: str) -> str:
    """Lower case without accents, the form the checklist patterns are written for."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ß", "ss"))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def documents() -> list[dict]:
    return json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))


def reference(name: str) -> dict:
    return json.loads((REFERENCE / f"{name}.json").read_text(encoding="utf-8"))


def coverage(name: str, title: str, summary: str) -> tuple[float, list[str]]:
    """Weighted share of the facts found, and the labels of the missing ones."""
    text = fold(f"{title}\n{summary}")
    found = total = 0
    missing = []
    for fact in reference(name)["facts"]:
        total += fact["weight"]
        if all(any(re.search(pattern, text) for pattern in group) for group in fact["all_of"]):
            found += fact["weight"]
        else:
            missing.append(fact["label"])
    return found / total, missing


def unsupported_numbers(source: str, summary: str) -> list[str]:
    """Numbers of two digits or more that the document does not contain."""
    known = {number.lstrip("0") for number in _NUMBER.findall(source)}
    squeezed = re.sub(r"(?<=\d)['’.,  ](?=\d)", "", source)
    found = []
    for number in _NUMBER.findall(re.sub(r"(?<=\d)['’  ](?=\d)", "", summary)):
        short = number.lstrip("0")
        if len(number) >= 2 and short not in known and number not in squeezed:
            if len(number) == 4 and number[2:] in known:  # 2026 written for a "26"
                continue
            found.append(number)
    return found


def is_french(text: str) -> bool:
    words = re.findall(r"[a-zàâéèêëîïôùûç]+", text.lower())
    return bool(words) and sum(word in _FRENCH for word in words) / len(words) > 0.08


def write_run(out: Path, docs: list[dict]) -> None:
    """A run directory as `scrape` leaves it, built from the stored documents."""
    sys.path.insert(0, str(ROOT / "src"))
    from uzh_crawler.store.crawl_json import CrawlManifest, Page, now_iso, save_manifest
    from uzh_crawler.text import content_hash, normalize_markdown

    (out / "raw").mkdir(parents=True)
    pages = []
    for doc in docs:
        markdown = doc.get("markdown") or (CORPUS / f"{doc['name']}.md").read_text(encoding="utf-8")
        (out / "raw" / f"{doc['name']}.md").write_text(markdown, encoding="utf-8")
        pages.append(
            Page(
                url=doc["url"], normalized_url=doc["url"], discovered_from=None, depth=0, status="fetched",
                raw_path=f"raw/{doc['name']}.md", content_hash=content_hash(normalize_markdown(markdown)),
            )
        )
    manifest = CrawlManifest(run_id="bench", seed_url=docs[0]["url"], started_at=now_iso(), config={}, pages=pages)
    save_manifest(out / "crawl.json", manifest)


def summarize(command: list[str], out: Path, extra: list[str]) -> tuple[float, str]:
    started = time.perf_counter()
    done = subprocess.run(
        [*command, "summarize", "--out", str(out), *extra], cwd=ROOT, capture_output=True, text=True
    )
    return time.perf_counter() - started, done.stderr


def unload(extra: list[str]) -> None:
    """Unload the model, so that no run reads prompts Ollama cached during an earlier one."""
    options = dict(zip(extra, extra[1:]))
    model = options.get("--model", DEFAULT_MODEL)
    base = options.get("--ollama-url", "http://localhost:11434").rstrip("/")
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    urllib.request.urlopen(f"{base}/api/generate", data=body, timeout=60).read()
    for _ in range(100):
        loaded = json.load(urllib.request.urlopen(f"{base}/api/ps", timeout=10))["models"]
        if model not in [entry["name"] for entry in loaded]:
            return
        time.sleep(0.2)


def warm_up(command: list[str], extra: list[str], directory: Path) -> None:
    """Load the model, with the same options, on a document that is not measured."""
    unload(extra)
    text = "# Bibliothek\n\n" + "Die Bibliothek des Instituts ist von Montag bis Freitag geöffnet. " * 8
    out = directory / "warmup"
    write_run(out, [{"name": "warmup", "url": "https://www.uzh.ch/warmup.html", "markdown": text}])
    summarize(command, out, extra)


def check(out: Path, docs: list[dict]) -> dict:
    """Quality of one finished run."""
    manifest = json.loads((out / "crawl.json").read_text(encoding="utf-8"))
    pages = {page["url"]: page for page in manifest["pages"]}
    scores: dict[str, float] = {}
    problems: list[str] = []
    invented: dict[str, list[str]] = {}
    words = 0
    shutil.rmtree(LAST, ignore_errors=True)
    LAST.mkdir(parents=True)
    for doc in docs:
        name = doc["name"]
        page = pages[doc["url"]]
        if page.get("status") != "summarized":
            problems.append(f"not summarized: {name} ({page.get('status')}: {page.get('error')})")
            scores[name] = 0.0
            continue
        document = (out / page["summary_path"]).read_text(encoding="utf-8")
        (LAST / f"{name}.md").write_text(document, encoding="utf-8")
        body = document.split("\n---\n", 1)[1]
        words += len(body.split())
        scores[name], missing = coverage(name, page["title"], body)
        (LAST / f"{name}.missing.txt").write_text("\n".join(missing), encoding="utf-8")
        source = (out / page["raw_path"]).read_text(encoding="utf-8") + " " + doc["url"]
        numbers = unsupported_numbers(source, f"{page['title']}\n{body}")
        if numbers:
            invented[name] = numbers
        if not is_french(body):
            problems.append(f"not French: {name}")
    mean = statistics.mean(scores.values())
    worst = min(scores, key=scores.get)
    count = sum(len(numbers) for numbers in invented.values())
    if len(scores) == len(documents()):
        if mean < MIN_COVERAGE:
            problems.append(f"coverage {mean:.3f} < {MIN_COVERAGE}")
        if scores[worst] < MIN_DOC_COVERAGE:
            problems.append(f"coverage of {worst} {scores[worst]:.3f} < {MIN_DOC_COVERAGE}")
        if count > MAX_UNSUPPORTED:
            problems.append(f"{count} unsupported numbers: {invented}")
    return {
        "coverage": round(mean, 3), "worst": round(scores[worst], 3), "worst_doc": worst,
        "unsupported": count, "words": words, "problems": problems,
        "scores": {name: round(score, 2) for name, score in scores.items()},
    }


def score_reference() -> int:
    total = []
    for doc in documents():
        ref = reference(doc["name"])
        score, missing = coverage(doc["name"], ref["title"], ref["resume_markdown"])
        source = (CORPUS / f"{doc['name']}.md").read_text(encoding="utf-8") + " " + doc["url"]
        numbers = unsupported_numbers(source, f"{ref['title']}\n{ref['resume_markdown']}")
        total.append(score)
        print(f"{doc['name']:18} facts={len(ref['facts']):2} coverage={score:.2f} "
              f"words={len(ref['resume_markdown'].split()):4} unsupported={numbers} missing={missing}")
    print(f"mean {statistics.mean(total):.3f}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    extra: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, extra = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--label", help="record the result in results.jsonl under this name")
    parser.add_argument("--command", help="run this instead of python -m uzh_crawler")
    parser.add_argument("--docs", help="comma-separated names: only these documents (no coverage floor)")
    parser.add_argument("--score-reference", action="store_true")
    args = parser.parse_args(argv)
    if args.score_reference:
        return score_reference()

    command = shlex.split(args.command) if args.command else DEFAULT_COMMAND
    docs = documents()
    if args.docs:
        docs = [doc for doc in docs if doc["name"] in args.docs.split(",")]
    times: list[float] = []
    checks: list[dict] = []
    for index in range(args.runs):
        with tempfile.TemporaryDirectory(prefix="summarize-bench-") as temporary:
            directory = Path(temporary)
            warm_up(command, extra, directory)
            out = directory / "run"
            write_run(out, docs)
            seconds, stderr = summarize(command, out, extra)
            result = check(out, docs)
        times.append(seconds)
        checks.append(result)
        verdict = "WRONG" if result["problems"] else "ok"
        print(f"  run {index + 1}: {seconds:.1f}s  coverage={result['coverage']} worst={result['worst']} "
              f"({result['worst_doc']}) unsupported={result['unsupported']} words={result['words']}  {verdict}",
              file=sys.stderr)
        for problem in result["problems"][:8]:
            print(f"    {problem}", file=sys.stderr)
        if result["problems"] and any("not summarized" in p for p in result["problems"]):
            print(stderr[-600:], file=sys.stderr)

    record = {
        "label": args.label, "task": "summarize", "median": round(statistics.median(times), 1),
        "min": round(min(times), 1), "max": round(max(times), 1), "runs": args.runs,
        "wrong": sum(bool(c["problems"]) for c in checks), "docs": len(docs),
        "coverage": round(statistics.mean(c["coverage"] for c in checks), 3),
        "worst": min(c["worst"] for c in checks),
        "unsupported": max(c["unsupported"] for c in checks),
        "words": round(statistics.mean(c["words"] for c in checks)),
        "extra": " ".join(extra), "command": args.command, "scores": checks[-1]["scores"],
    }
    print(json.dumps({k: v for k, v in record.items() if k != "scores"}, ensure_ascii=False))
    if args.label:
        with RESULTS.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 1 if record["wrong"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
