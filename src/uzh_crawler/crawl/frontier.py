from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlsplit

from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.crawl.fetch import conditional_validators, fetch_url, request_host
from uzh_crawler.crawl.js_fallback import render_page
from uzh_crawler.crawl.limiter import HostRateLimiter
from uzh_crawler.crawl.robots import RobotsCache
from uzh_crawler.store.crawl_json import Page, save_manifest
from uzh_crawler.store.paths import links_relative, raw_relative, safe_join
from uzh_crawler.urls import url_in_scope

logger = logging.getLogger(__name__)

# Static assets are not documents. They are neither fetched nor tagged.
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
_SKIP_REFETCH = {"blocked_auth", "out_of_scope"}
_ROBOTS_ERROR = "disallowed by robots.txt"


async def crawl_frontier(config: RunConfig, manifest, client, run_dir: Path) -> None:
    """Breadth-first crawl. Already stored pages are revalidated with validators."""
    queue: asyncio.Queue[tuple[str, str | None, int] | None] = asyncio.Queue()
    seen: set[str] = set()
    lock = asyncio.Lock()
    fetches = {"n": 0}
    limiter = HostRateLimiter(config.rate_limit)
    robots = RobotsCache(
        client,
        USER_AGENT,
        ignore=config.ignore_robots,
        limiter=limiter,
    )
    render = render_page if config.js_fallback else None

    def persist() -> None:
        save_manifest(run_dir / "crawl.json", manifest)

    async def enqueue(url: str, parent: str | None, depth: int) -> None:
        if config.max_depth is not None and depth > config.max_depth:
            return
        if _is_asset(url):
            return
        async with lock:
            if url in seen:
                return
            seen.add(url)
        await queue.put((url, parent, depth))

    async def note_out_of_scope(url: str, parent: str | None, depth: int) -> None:
        async with lock:
            if url in seen:
                return
            seen.add(url)
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
                logger.info("out_of_scope %s", url)

    async def remember_failure(url: str, parent: str | None, depth: int, error: str) -> None:
        async with lock:
            existing = manifest.page(url)
            if existing and existing.status in {"digest_ok", "fetched", "empty"} and existing.raw_path:
                existing.error = error
                persist()
                return
            manifest.upsert(_failed_page(url, parent, depth, existing, error))
            persist()
            logger.info("failed %s (%s)", url, error)

    async def process(url: str, parent: str | None, depth: int) -> None:
        existing = manifest.page(url)
        if existing and existing.status in _SKIP_REFETCH:
            return
        try:
            scoped = url_in_scope(url, config.seed_url, config.allow_subdomains)
        except ValueError as exc:
            await remember_failure(url, parent, depth, str(exc))
            return
        if not scoped:
            await note_out_of_scope(url, parent, depth)
            return
        if not await robots.allowed(url):
            await remember_failure(url, parent, depth, _ROBOTS_ERROR)
            return
        async with lock:
            if fetches["n"] >= config.max_pages:
                return
            fetches["n"] += 1
        await limiter.wait(request_host(url))
        etag, modified = conditional_validators(manifest.page(url))
        outcome = await fetch_url(
            client,
            url,
            seed_url=config.seed_url,
            allow_subdomains=config.allow_subdomains,
            etag=etag,
            last_modified=modified,
            render_js=render,
        )
        async with lock:
            links = _apply(manifest, run_dir, url, parent, depth, outcome)
            persist()
        label = outcome.status
        logger.info("%s %s", label, url)
        for link in links:
            try:
                in_scope = url_in_scope(link, config.seed_url, config.allow_subdomains)
            except ValueError:
                continue
            if in_scope:
                await enqueue(link, url, depth + 1)
            else:
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
                    await remember_failure(url, parent, depth, f"{type(exc).__name__}: {exc}")
            finally:
                queue.task_done()

    await enqueue(config.seed_url, None, 0)
    workers = [
        asyncio.create_task(worker())
        for _ in range(max(config.crawl_concurrency, 1))
    ]
    await queue.join()
    for _ in workers:
        await queue.put(None)
    await asyncio.gather(*workers)


def _apply(manifest, run_dir: Path, url: str, parent: str | None, depth: int, outcome) -> list[str]:
    from uzh_crawler.store.crawl_json import now_iso

    existing = manifest.page(url)
    discovered = None if existing is None else existing.discovered_from
    if discovered is None:
        discovered = parent
    known_depth = depth if existing is None else existing.depth

    if outcome.status == "not_modified":
        if existing is None or not existing.raw_path:
            manifest.upsert(
                _failed_page(url, parent, depth, existing, "304 without a stored copy")
            )
            return []
        existing.fetched_at = now_iso()
        if outcome.etag:
            existing.etag = outcome.etag
        if outcome.last_modified:
            existing.last_modified = outcome.last_modified
        existing.error = None
        return _read_links(run_dir, existing.raw_path)

    if outcome.status == "out_of_scope":
        manifest.upsert(
            Page(
                url=url,
                normalized_url=url,
                discovered_from=discovered,
                depth=known_depth,
                status="out_of_scope",
                http_status=outcome.http_status,
                content_type=outcome.content_type,
                fetched_at=now_iso(),
                error=outcome.error,
            )
        )
        return []

    if outcome.status in {"failed", "blocked_auth"}:
        if existing and existing.status in {"digest_ok", "fetched", "empty"} and existing.raw_path:
            existing.error = outcome.error
            existing.fetched_at = now_iso()
            existing.http_status = outcome.http_status
            return _read_links(run_dir, existing.raw_path)
        manifest.upsert(
            Page(
                url=url,
                normalized_url=url,
                discovered_from=discovered,
                depth=known_depth,
                status=outcome.status,
                http_status=outcome.http_status,
                content_type=outcome.content_type,
                fetched_at=now_iso(),
                error=outcome.error,
            )
        )
        return []

    markdown = outcome.markdown or ""
    links = list(outcome.links or [])
    raw_path = _write_raw(run_dir, url, markdown, links)
    unchanged = (
        existing is not None
        and existing.content_hash == outcome.content_hash
        and existing.status == "digest_ok"
    )
    if unchanged:
        existing.etag = outcome.etag
        existing.last_modified = outcome.last_modified
        existing.fetched_at = now_iso()
        existing.http_status = outcome.http_status
        existing.content_type = outcome.content_type
        existing.raw_path = raw_path
        existing.title = outcome.title or existing.title
        existing.error = None
        return links

    manifest.upsert(
        Page(
            url=url,
            normalized_url=url,
            discovered_from=discovered,
            depth=known_depth,
            status=outcome.status,
            http_status=outcome.http_status,
            content_type=outcome.content_type,
            fetched_at=now_iso(),
            raw_path=raw_path,
            content_hash=outcome.content_hash,
            etag=outcome.etag,
            last_modified=outcome.last_modified,
            title=outcome.title,
            digest_path=None,
            tags=None,
            error=outcome.error,
        )
    )
    return links


def _failed_page(url: str, parent: str | None, depth: int, existing: Page | None, error: str) -> Page:
    return Page(
        url=url,
        normalized_url=url,
        discovered_from=parent if existing is None else existing.discovered_from,
        depth=depth if existing is None else existing.depth,
        status="failed",
        http_status=None if existing is None else existing.http_status,
        error=error,
    )


def _write_raw(run_dir: Path, url: str, markdown: str, links: list[str]) -> str:
    relative = raw_relative(url)
    target = safe_join(run_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = markdown if (not markdown or markdown.endswith("\n")) else f"{markdown}\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    link_path = safe_join(run_dir, links_relative(relative))
    payload = "\n".join(links)
    if payload:
        payload += "\n"
    link_path.write_text(payload, encoding="utf-8", newline="\n")
    return relative


def _read_links(run_dir: Path, raw_path: str | None) -> list[str]:
    if not raw_path:
        return []
    try:
        path = safe_join(run_dir, links_relative(raw_path))
    except ValueError:
        return []
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_asset(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(_ASSET_SUFFIXES)
