"""Sitemap discovery: robots.txt, the usual paths, text and XML indexes.

Pages found here are extra start URLs. They are still filtered by the same
host, prefix and exclusion rules as a link found while crawling.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from uzh_crawler.crawl.limiter import HostRateLimiter
from uzh_crawler.urls import try_normalize

logger = logging.getLogger(__name__)

_COMMON_PATHS = ("/sitemap.txt", "/sitemap.xml", "/sitemap_index.xml")
_MAX_SITEMAP_DOCUMENTS = 32


def parse_robots_sitemaps(body: str) -> list[str]:
    urls: list[str] = []
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip().lower() != "sitemap":
            continue
        url = value.strip()
        if url:
            urls.append(url)
    return urls


def parse_sitemap(body: bytes, *, content_type: str | None) -> tuple[list[str], list[str]]:
    """Return ``(page URLs, nested sitemap URLs)``."""
    ctype = (content_type or "").lower()
    looks_xml = "xml" in ctype or body[:64].lstrip().startswith(
        (b"<?xml", b"<urlset", b"<sitemapindex")
    )
    if looks_xml:
        return _parse_xml_sitemap(body)
    return _parse_text_sitemap(body), []


def _parse_text_sitemap(body: bytes) -> list[str]:
    urls: list[str] = []
    for raw_line in body.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _parse_xml_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return [], []
    tag = _local_name(root.tag)
    if tag not in {"urlset", "sitemapindex"}:
        return [], []
    locs: list[str] = []
    for entry in root:
        found = [
            child.text.strip()
            for child in entry
            if _local_name(child.tag) == "loc" and child.text and child.text.strip()
        ]
        if len(found) == 1:
            locs.append(found[0])
    if tag == "sitemapindex":
        return [], locs
    return locs, []


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


async def discover_sitemap_pages(
    client: httpx.AsyncClient,
    origins: list[str],
    *,
    limiter: HostRateLimiter,
    accept,
    robots=None,
) -> list[tuple[str, str]]:
    """Return ``(page URL, sitemap URL)`` for accepted pages.

    ``accept`` receives a normalized URL and returns whether it belongs in
    the section. A sitemap that fails to load is skipped.

    Sitemaps are fetched in waves: every document known at one moment is
    requested at the same time, then the indexes among them give the next wave.
    """
    wave = await _sitemap_documents(client, origins, limiter, robots)
    seen_documents: set[str] = set()
    pages: list[tuple[str, str]] = []
    seen_pages: set[str] = set()
    while wave and len(seen_documents) < _MAX_SITEMAP_DOCUMENTS:
        batch: list[str] = []
        for sitemap_url in wave:
            key = try_normalize(sitemap_url) or sitemap_url
            if key in seen_documents or len(seen_documents) >= _MAX_SITEMAP_DOCUMENTS:
                continue
            seen_documents.add(key)
            batch.append(sitemap_url)
        loaded = await asyncio.gather(*(_fetch_sitemap(client, url, limiter) for url in batch))
        wave = []
        for sitemap_url, result in zip(batch, loaded, strict=True):
            if result is None:
                continue
            body, content_type = result
            page_urls, nested = parse_sitemap(body, content_type=content_type)
            wave.extend(nested)
            for raw in page_urls:
                normalized = try_normalize(raw)
                if normalized is None or normalized in seen_pages:
                    continue
                if not accept(normalized):
                    continue
                seen_pages.add(normalized)
                pages.append((normalized, sitemap_url))
    return pages


async def _sitemap_documents(
    client: httpx.AsyncClient,
    origins: list[str],
    limiter: HostRateLimiter,
    robots=None,
) -> list[str]:
    """Where to look first: what each robots.txt declares, then the usual paths.

    ``robots`` is the crawl's RobotsCache. Using it means robots.txt is
    downloaded once, for the sitemap lines and for the access rules alike.
    """
    documents: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        normalized = try_normalize(url)
        if normalized is None or normalized in seen:
            return
        seen.add(normalized)
        documents.append(normalized)

    async def robots_text(origin: str) -> str | None:
        if robots is not None:
            return await robots.text(origin)
        loaded = await _fetch_sitemap(client, urljoin(origin, "/robots.txt"), limiter)
        return None if loaded is None else loaded[0].decode("utf-8", errors="replace")

    texts = await asyncio.gather(*(robots_text(origin) for origin in origins))
    for origin, text in zip(origins, texts, strict=True):
        if text:
            for url in robots_sitemap_urls(text, origin):
                add(url)
        for path in _COMMON_PATHS:
            add(urljoin(origin, path))
    return documents


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


async def _fetch_sitemap(
    client: httpx.AsyncClient,
    url: str,
    limiter: HostRateLimiter,
) -> tuple[bytes, str | None] | None:
    await limiter.wait(urlsplit(url).hostname or "")
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("sitemap ignoré %s (%s)", url, type(exc).__name__)
        return None
    return response.content, response.headers.get("content-type")


def robots_sitemap_urls(body: str, origin: str) -> list[str]:
    """Absolute sitemap URLs declared by a robots.txt body."""
    urls: list[str] = []
    for raw in parse_robots_sitemaps(body):
        absolute = raw if "://" in raw else urljoin(origin, raw)
        normalized = try_normalize(absolute)
        if normalized is not None:
            urls.append(normalized)
    return urls
