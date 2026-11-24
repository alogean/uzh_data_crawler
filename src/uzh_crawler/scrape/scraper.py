"""Scraping: download a URL, keep the original file, and extract its text.

``files/`` holds the bytes as served. ``raw/`` holds the Markdown extracted
from them, which is what the summary stage reads.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.crawl.discover import ThrottledSaver, write_links
from uzh_crawler.crawl.limiter import HostRateLimiter
from uzh_crawler.crawl.robots import RobotsCache
from uzh_crawler.progress import Progress
from uzh_crawler.scope import Scope, same_site
from uzh_crawler.scrape.extract import MIN_USEFUL_CHARS, extract_html, extract_pdf
from uzh_crawler.scrape.fetch import (
    conditional_validators,
    decode_text,
    download,
    media_type,
    sniff_kind,
)
from uzh_crawler.scrape.js_fallback import render_page
from uzh_crawler.store.crawl_json import ROBOTS_ERROR, CrawlManifest, Page, now_iso
from uzh_crawler.store.paths import file_relative, raw_relative, safe_join
from uzh_crawler.text import content_hash, normalize_markdown
from uzh_crawler.urls import host_of, try_normalize

logger = logging.getLogger(__name__)


@dataclass
class Extracted:
    kind: str
    markdown: str = ""
    title: str | None = None
    links: list[str] = field(default_factory=list)
    error: str | None = None


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

    def report(status: str, url: str) -> None:
        if progress is not None:
            progress.tick(status, url)

    async def one(page: Page) -> None:
        url = page.normalized_url
        async with semaphore:
            try:
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

        local = _local_copy(run_dir, page) if not config.refresh else None
        if local is not None:
            body, ctype, final = local, page.content_type, url
            shown = "copie locale"
        else:
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
            shown = None

        kind = sniff_kind(url, ctype, body)
        if local is None:
            page.file_path = _store_file(run_dir, url, kind, body)
        extracted = await _extract(kind, body, ctype, final or url, url, render)
        page.fetched_at = now_iso()
        if extracted.kind == "failed":
            _fail(page, extracted.error or "extraction failed")
            return "failed"

        markdown = normalize_markdown(extracted.markdown)
        new_hash = content_hash(markdown)
        page.raw_path = _store_markdown(run_dir, url, markdown)
        if kind == "html":
            write_links(run_dir, url, extracted.links)
        page.title = extracted.title or page.title
        page.error = extracted.error
        if len(markdown) < MIN_USEFUL_CHARS:
            page.status = "empty"
            page.clear_summary()
        elif page.content_hash == new_hash:
            page.status = page.settled_status()
        else:
            page.status = "fetched"
            page.clear_summary()
        page.content_hash = new_hash
        return shown or page.status

    await asyncio.gather(*(one(page) for page in pages))
    persist.flush()


async def _extract(kind: str, body: bytes, ctype: str | None, base_url: str, url: str, render) -> Extracted:
    if kind == "pdf":
        try:
            return Extracted(kind="pdf", markdown=extract_pdf(body))
        except ValueError as exc:
            return Extracted(kind="failed", error=str(exc))
    if kind == "text":
        return Extracted(kind="text", markdown=decode_text(body, ctype))
    if kind != "html":
        # The file is kept under files/, but nothing here can read its text.
        return Extracted(kind=kind, error=f"no text extractor for {media_type(ctype) or 'unknown type'}")
    html = extract_html(decode_text(body, ctype), base_url)
    result = Extracted(kind="html", markdown=html.markdown, title=html.title, links=html.links)
    if len(result.markdown) < MIN_USEFUL_CHARS and render is not None:
        rendered = await render(url)
        if not rendered:
            result.error = "js fallback produced no HTML"
            return result
        again = extract_html(rendered, base_url)
        if len(again.markdown) >= len(result.markdown):
            result.markdown = again.markdown
            result.title = again.title or result.title
            result.links = again.links or result.links
    return result


def _redirect_ok(scope: Scope, url: str, final: str, explicit: bool) -> bool:
    if explicit:
        return same_site(host_of(url), host_of(final))
    return scope.classify(final) == "in"


def _local_copy(run_dir: Path, page: Page) -> bytes | None:
    """The HTML that discovery already downloaded, when the page was not scraped yet."""
    if page.status != "discovered" or not page.file_path:
        return None
    try:
        path = safe_join(run_dir, page.file_path)
    except ValueError:
        return None
    return path.read_bytes() if path.is_file() else None


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
