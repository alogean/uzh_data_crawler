"""Discovery: list every URL of a site without scraping it.

Sitemaps give most of the list in a few requests. Link following completes it:
HTML pages are read for their links, and linked documents (PDF, Office) are
listed without being downloaded. The HTML that had to be read is kept under
``files/`` so the scrape stage does not download it a second time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.crawl.limiter import HostRateLimiter
from uzh_crawler.crawl.links import extract_links
from uzh_crawler.crawl.robots import RobotsCache
from uzh_crawler.crawl.sitemap import discover_sitemap_pages, origin_of
from uzh_crawler.progress import Progress
from uzh_crawler.scope import Scope, frontier_dedupe_key
from uzh_crawler.scrape.fetch import (
    conditional_validators,
    decode_text,
    download,
    is_document_url,
    sniff_kind,
)
from uzh_crawler.store.crawl_json import (
    ROBOTS_ERROR,
    STORED_STATUSES,
    CrawlManifest,
    Page,
    save_manifest,
)
from uzh_crawler.store.paths import file_relative, links_relative, safe_join
from uzh_crawler.urls import host_of, normalize_url, try_normalize

logger = logging.getLogger(__name__)

# Static assets are not documents. They are neither listed nor fetched.
_ASSET_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".mp4",
    ".mp3",
    ".wav",
    ".webm",
    ".zip",
    ".gz",
    ".tgz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
)
_SKIP_RECHECK = {"blocked_auth", "out_of_scope"}
_SAVE_INTERVAL = 2.0


class ThrottledSaver:
    """Write crawl.json at most every few seconds; the caller saves once at the end."""

    def __init__(self, path: Path, manifest: CrawlManifest, interval: float = _SAVE_INTERVAL):
        self.path = path
        self.manifest = manifest
        self.interval = interval
        # The clock starts now: the caller has just written the file once.
        self._last = time.monotonic()

    def __call__(self) -> None:
        now = time.monotonic()
        if now - self._last >= self.interval:
            self.flush()

    def flush(self) -> None:
        save_manifest(self.path, self.manifest)
        self._last = time.monotonic()


async def discover(
    config: RunConfig,
    scope: Scope,
    manifest: CrawlManifest,
    client,
    run_dir: Path,
    progress: Progress | None = None,
) -> None:
    """Breadth-first discovery. Every in-scope URL ends up in the manifest."""
    queue: asyncio.Queue[tuple[str, str | None, int] | None] = asyncio.Queue()
    seen: set[str] = set()
    lock = asyncio.Lock()
    fetches = {"n": 0}
    limiter = HostRateLimiter(config.rate_limit, config.burst)
    robots = RobotsCache(client, USER_AGENT, ignore=config.ignore_robots, limiter=limiter)
    persist = ThrottledSaver(run_dir / "crawl.json", manifest)

    def report(status: str, url: str) -> None:
        if progress is not None:
            progress.tick(status, url)

    async def enqueue(url: str, parent: str | None, depth: int) -> None:
        if config.max_depth is not None and depth > config.max_depth:
            return
        if is_asset(url):
            return
        try:
            key = frontier_dedupe_key(url)
            normalized = normalize_url(url)
        except ValueError:
            return
        async with lock:
            if key in seen:
                return
            seen.add(key)
        await queue.put((normalized, parent, depth))
        if progress is not None and config.max_pages is None:
            progress.set_total(progress.done + queue.qsize())

    async def note_out_of_scope(url: str, parent: str | None, depth: int) -> None:
        try:
            key = frontier_dedupe_key(url)
        except ValueError:
            key = url
        async with lock:
            if key in seen:
                return
            seen.add(key)
            if manifest.page(url) is None:
                manifest.upsert(
                    Page(
                        url=url,
                        normalized_url=url,
                        discovered_from=parent,
                        depth=depth,
                        status="out_of_scope",
                        error="host is outside the crawl scope",
                    )
                )
                persist()

    async def remember(url: str, parent: str | None, depth: int, status: str, **fields) -> None:
        """Record what discovery learned. A page that already has a copy keeps its status."""
        async with lock:
            existing = manifest.page(url)
            if existing is None:
                manifest.upsert(
                    Page(
                        url=url,
                        normalized_url=url,
                        discovered_from=parent,
                        depth=depth,
                        status=status,
                        **fields,
                    )
                )
            else:
                keeps_copy = existing.status in STORED_STATUSES and existing.raw_path
                if not keeps_copy or status == "out_of_scope":
                    existing.status = status
                for key, value in fields.items():
                    if value is not None or key == "error":
                        setattr(existing, key, value)
                if status == "discovered" and "error" not in fields:
                    existing.error = None
            persist()

    async def process(url: str, parent: str | None, depth: int) -> None:
        existing = manifest.page(url)
        if existing and existing.status in _SKIP_RECHECK:
            report("déjà classé", url)
            return
        kind = scope.classify(url)
        if kind == "foreign":
            await note_out_of_scope(url, parent, depth)
            report("hors périmètre", url)
            return
        if kind != "in":
            report("hors section", url)
            return
        if not await robots.allowed(url):
            await remember(url, parent, depth, "failed", error=ROBOTS_ERROR)
            report("robots", url)
            return
        at_depth_limit = config.max_depth is not None and depth >= config.max_depth
        if config.sitemap_only or at_depth_limit or is_document_url(url):
            # Listed without a request: a document, or a page whose links would
            # not be followed anyway. Reading it is the scrape stage's job.
            await remember(url, parent, depth, "discovered")
            report("listé", url)
            return
        async with lock:
            if config.max_pages is not None and fetches["n"] >= config.max_pages:
                # Known, not read: it stays in the list for a later crawl or scrape.
                over_ceiling = True
            else:
                over_ceiling = False
                fetches["n"] += 1
        if over_ceiling:
            await remember(url, parent, depth, "discovered")
            return
        await limiter.wait(host_of(url))
        etag, modified = _validators(run_dir, existing)
        result = await download(client, url, etag=etag, last_modified=modified, html_only=True)

        final = try_normalize(result.final_url) if result.final_url else None
        if final and final != url and result.kind in {"ok", "not_html"}:
            landed = scope.classify(final)
            if landed != "in":
                if landed == "foreign":
                    await note_out_of_scope(final, url, depth)
                    error = f"redirected outside the crawl scope: {final}"
                else:
                    error = "redirect left the section"
                await remember(
                    url, parent, depth, "out_of_scope", http_status=result.http_status, error=error
                )
                report("hors section", url)
                return

        if result.kind == "not_modified":
            links = _read_links(run_dir, url)
            await remember(url, parent, depth, "discovered", etag=result.etag,
                           last_modified=result.last_modified)
            report("cache HTTP", url)
        elif result.kind in {"blocked_auth", "failed"}:
            await remember(
                url, parent, depth, result.kind, http_status=result.http_status,
                content_type=result.content_type, error=result.error,
            )
            report(result.kind, url)
            return
        elif result.kind == "not_html" or sniff_kind(url, result.content_type, result.body or b"") != "html":
            await remember(
                url, parent, depth, "discovered", http_status=result.http_status,
                content_type=result.content_type,
            )
            report("listé", url)
            return
        else:
            body = result.body or b""
            title, links = extract_links(decode_text(body, result.content_type), final or url)
            changed = _store_html(run_dir, url, body, links)
            async with lock:
                page = manifest.page(url)
                if page is not None and changed and page.status in STORED_STATUSES:
                    # The page changed since it was scraped: scrape reads the new copy.
                    page.status = "discovered"
            await remember(
                url, parent, depth, "discovered", http_status=result.http_status,
                content_type=result.content_type, etag=result.etag,
                last_modified=result.last_modified, title=title,
                file_path=file_relative(url, "html"),
            )
            report("lu", url)

        for link in links:
            kind = scope.classify(link)
            if kind == "in":
                await enqueue(link, url, depth + 1)
            elif kind == "foreign":
                await note_out_of_scope(link, url, depth + 1)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                try:
                    await process(*item)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    url, parent, depth = item
                    logger.exception("crawl error %s", url)
                    await remember(url, parent, depth, "failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                queue.task_done()

    for seed in config.seed_urls:
        await enqueue(seed, None, 0)
    if config.discover_sitemaps:
        if progress is not None:
            progress.note("lecture des sitemaps")
        origins = list(dict.fromkeys(origin_of(seed) for seed in config.seed_urls))
        found = await discover_sitemap_pages(
            client,
            origins,
            limiter=limiter,
            accept=lambda url: scope.classify(url) == "in" and not is_asset(url),
            robots=robots,
        )
        if progress is not None:
            progress.note(f"{len(found)} URL de sitemap dans le périmètre")
        for page_url, sitemap_url in found:
            await enqueue(page_url, sitemap_url, 0)
        if progress is not None and config.max_pages is not None:
            progress.set_total(queue.qsize())
    workers = [
        asyncio.create_task(worker())
        for _ in range(max(config.crawl_concurrency, 1))
    ]
    await queue.join()
    for _ in workers:
        await queue.put(None)
    await asyncio.gather(*workers)
    persist.flush()


def write_url_list(run_dir: Path, manifest: CrawlManifest) -> int:
    """Write urls.txt: every URL of the run that is in scope, one per line."""
    urls = sorted(page.normalized_url for page in manifest.pages if page.status != "out_of_scope")
    payload = "\n".join(urls)
    if payload:
        payload += "\n"
    (run_dir / "urls.txt").write_text(payload, encoding="utf-8", newline="\n")
    return len(urls)


def _validators(run_dir: Path, page: Page | None) -> tuple[str | None, str | None]:
    """Conditional headers, only when the links of the stored copy can be read back."""
    if page is None:
        return None, None
    try:
        if not safe_join(run_dir, links_relative(page.normalized_url)).is_file():
            return None, None
    except ValueError:
        return None, None
    return conditional_validators(page)


def _store_html(run_dir: Path, url: str, body: bytes, links: list[str]) -> bool:
    """Keep the HTML and its links. Returns whether the bytes differ from the stored copy."""
    target = safe_join(run_dir, file_relative(url, "html"))
    target.parent.mkdir(parents=True, exist_ok=True)
    changed = not target.is_file() or target.read_bytes() != body
    if changed:
        target.write_bytes(body)
    write_links(run_dir, url, links)
    return changed


def write_links(run_dir: Path, url: str, links: list[str]) -> None:
    path = safe_join(run_dir, links_relative(url))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(links)
    if payload:
        payload += "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def _read_links(run_dir: Path, url: str) -> list[str]:
    try:
        path = safe_join(run_dir, links_relative(url))
    except ValueError:
        return []
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def is_asset(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(_ASSET_SUFFIXES)
