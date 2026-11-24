"""Scraping: download a URL, keep the original file, and extract its text.

``files/`` holds the bytes as served. ``raw/`` holds the Markdown extracted
from them, which is what the summary stage reads.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.crawl.discover import ThrottledSaver, write_links
from uzh_crawler.crawl.limiter import HostRateLimiter
from uzh_crawler.crawl.robots import RobotsCache
from uzh_crawler.progress import Progress
from uzh_crawler.scope import Scope, same_site
from uzh_crawler.scrape.extract import MIN_USEFUL_CHARS, convert
from uzh_crawler.scrape.fetch import (
    conditional_validators,
    download,
    sniff_kind,
)
from uzh_crawler.scrape.js_fallback import render_page
from uzh_crawler.store.crawl_json import ROBOTS_ERROR, CrawlManifest, Page, now_iso
from uzh_crawler.store.paths import file_relative, raw_relative, safe_join
from uzh_crawler.text import content_hash, normalize_markdown
from uzh_crawler.urls import host_of, try_normalize

logger = logging.getLogger(__name__)

DOCUMENTS_PER_WORKER = 4


async def scrape_pages(
    config: RunConfig,
    scope: Scope,
    manifest: CrawlManifest,
    client,
    run_dir: Path,
    pages: list[Page],
    progress: Progress | None = None,
    *,
    explicit: bool = False,
) -> None:
    """Scrape the given pages. ``explicit`` pages were named by the user, not found by a crawl."""
    limiter = HostRateLimiter(config.rate_limit, config.burst)
    robots = RobotsCache(client, USER_AGENT, ignore=config.ignore_robots, limiter=limiter)
    render = render_page if config.js_fallback else None
    persist = ThrottledSaver(run_dir / "crawl.json", manifest)
    semaphore = asyncio.Semaphore(max(config.crawl_concurrency, 1))
    downloads = {"n": 0}
    pool = _worker_pool(config, len(pages))
    loop = asyncio.get_running_loop()

    root = str(run_dir)

    async def run(url: str, **source) -> Converted:
        if pool is None:
            return convert_document(root, url, **source)
        return await loop.run_in_executor(pool, functools.partial(convert_document, root, url, **source))

    def report(status: str, url: str) -> None:
        if progress is not None:
            progress.tick(status, url)

    async def one(page: Page) -> None:
        url = page.normalized_url
        try:
            if not config.refresh and _has_local_copy(run_dir, page):
                # Nothing to download: the worker reads the file the crawl stored.
                status = await _finish(page, url, "copie locale", file_path=page.file_path, content_type=page.content_type)
            else:
                async with semaphore:
                    status = await _scrape_one(page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("scrape error %s", url)
            _fail(page, f"{type(exc).__name__}: {exc}")
            status = "failed"
        persist()
        if status is not None:
            report(status, url)

    async def _scrape_one(page: Page) -> str | None:
        url = page.normalized_url
        if not await robots.allowed(url):
            _fail(page, ROBOTS_ERROR)
            return "robots"
        if config.max_pages is not None and downloads["n"] >= config.max_pages:
            return None
        downloads["n"] += 1
        await limiter.wait(host_of(url))
        # A 304 is only useful when both the text and the original are on disk.
        has_copy = bool(page.raw_path) and _has_file(run_dir, page)
        etag, modified = conditional_validators(page) if has_copy else (None, None)
        result = await download(client, url, etag=etag, last_modified=modified)
        if result.kind == "not_modified":
            page.fetched_at = now_iso()
            page.etag = result.etag or page.etag
            page.last_modified = result.last_modified or page.last_modified
            page.error = None
            if page.status == "discovered":
                page.status = page.settled_status()
            return "cache HTTP"
        if result.kind in {"blocked_auth", "failed"}:
            page.http_status = result.http_status
            page.fetched_at = now_iso()
            if page.raw_path:
                # The stored copy stays usable; only the failure is noted.
                page.error = result.error
                if page.status == "discovered":
                    page.status = page.settled_status()
            else:
                page.status = result.kind
                page.content_type = result.content_type
                page.error = result.error
            return result.kind
        final = try_normalize(result.final_url) if result.final_url else url
        if final and final != url and not _redirect_ok(scope, url, final, explicit):
            page.status = "out_of_scope"
            page.http_status = result.http_status
            page.error = f"redirected outside the crawl scope: {final}"
            return "hors périmètre"
        body, ctype = result.body or b"", result.content_type
        page.http_status = result.http_status
        page.content_type = ctype
        page.etag = result.etag
        page.last_modified = result.last_modified
        page.file_path = _store_file(run_dir, url, sniff_kind(url, ctype, body), body)
        return await _finish(page, url, None, body=body, content_type=ctype, base_url=final or url)

    async def _finish(page: Page, url: str, shown: str | None, **source) -> str:
        """Convert the document, then record what the conversion gave."""
        converted = await run(url, **source)
        if converted.kind == "html" and converted.length < MIN_USEFUL_CHARS and render is not None:
            rendered = await render(url)
            if not rendered:
                converted.error = "js fallback produced no HTML"
            else:
                again = await run(
                    url,
                    body=rendered.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                    base_url=source.get("base_url") or url,
                    longer_than=converted.length,
                )
                if again.kind == "html":
                    again.title = again.title or converted.title
                    converted = again
        page.fetched_at = now_iso()
        if converted.kind == "failed":
            _fail(page, converted.error or "extraction failed")
            return "failed"
        page.raw_path = converted.raw_path
        page.title = converted.title or page.title
        page.error = converted.error
        if converted.length < MIN_USEFUL_CHARS:
            page.status = "empty"
            page.clear_summary()
        elif page.content_hash == converted.content_hash:
            page.status = page.settled_status()
        else:
            page.status = "fetched"
            page.clear_summary()
        page.content_hash = converted.content_hash
        return shown or page.status

    try:
        await asyncio.gather(*(one(page) for page in pages))
    finally:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
    persist.flush()


@dataclass
class Converted:
    """What a worker reports about one document. The Markdown itself is already on disk."""

    kind: str
    raw_path: str | None = None
    content_hash: str | None = None
    length: int = 0
    title: str | None = None
    error: str | None = None


def convert_document(
    run_dir: str,
    url: str,
    *,
    body: bytes | None = None,
    file_path: str | None = None,
    content_type: str | None = None,
    base_url: str | None = None,
    longer_than: int | None = None,
) -> Converted:
    """Convert one document to Markdown and store it under ``raw/``.

    This is the part of a scrape that needs the processor, so it is what runs
    in the worker processes. The document comes either as ``body``, the bytes
    just downloaded, or as ``file_path``, the file a crawl already stored. With
    ``longer_than``, a result that is shorter than that is reported as
    ``skipped`` and nothing is written.
    """
    root = Path(run_dir)
    if body is None:
        body = safe_join(root, file_path or "").read_bytes()
    kind = sniff_kind(url, content_type, body)
    extracted = convert(kind, body, content_type, base_url or url)
    if extracted.kind == "failed":
        return Converted(kind="failed", error=extracted.error)
    markdown = normalize_markdown(extracted.markdown)
    if longer_than is not None and len(markdown) < longer_than:
        return Converted(kind="skipped")
    raw_path = _store_markdown(root, url, markdown)
    if kind == "html" and (extracted.links or longer_than is None):
        write_links(root, url, extracted.links)
    return Converted(
        kind=kind,
        raw_path=raw_path,
        content_hash=content_hash(markdown),
        length=len(markdown),
        title=extracted.title,
        error=extracted.error,
    )


def _worker_pool(config: RunConfig, documents: int) -> ProcessPoolExecutor | None:
    """Worker processes for the conversion, when there is enough of it to share.

    Starting a worker costs more than converting a few pages, so a small scrape
    converts in the main process.
    """
    workers = config.extract_workers
    if workers is None:
        workers = os.cpu_count() or 1
    workers = min(workers, documents // DOCUMENTS_PER_WORKER)
    if workers < 2:
        return None
    # A fork starts a worker in a few milliseconds, but it is only safe while
    # this process has a single thread. Otherwise workers start from scratch.
    forkable = "fork" in multiprocessing.get_all_start_methods() and threading.active_count() == 1
    context = multiprocessing.get_context("fork" if forkable else "spawn")
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=context)
    if forkable:
        # Start every worker now, before the first request creates a thread.
        for _ in range(workers):
            pool.submit(_ready)
    return pool


def _ready() -> None:
    return None


def _redirect_ok(scope: Scope, url: str, final: str, explicit: bool) -> bool:
    if explicit:
        return same_site(host_of(url), host_of(final))
    return scope.classify(final) == "in"


def _has_local_copy(run_dir: Path, page: Page) -> bool:
    """Whether discovery already downloaded this page and it was not scraped yet."""
    return page.status == "discovered" and _has_file(run_dir, page)


def _has_file(run_dir: Path, page: Page) -> bool:
    if not page.file_path:
        return False
    try:
        return safe_join(run_dir, page.file_path).is_file()
    except ValueError:
        return False


def _store_file(run_dir: Path, url: str, kind: str, body: bytes) -> str:
    relative = file_relative(url, kind)
    target = safe_join(run_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return relative


def _store_markdown(run_dir: Path, url: str, markdown: str) -> str:
    relative = raw_relative(url)
    target = safe_join(run_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = markdown if (not markdown or markdown.endswith("\n")) else f"{markdown}\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    return relative


def _fail(page: Page, error: str) -> None:
    if page.raw_path and page.status != "discovered":
        page.error = error
        return
    page.status = "failed"
    page.error = error
