from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from uzh_crawler.config import USER_AGENT
from uzh_crawler.crawl.extract import MIN_USEFUL_CHARS, extract_html, extract_pdf
from uzh_crawler.text import content_hash, normalize_markdown
from uzh_crawler.urls import host_of, normalize_url, try_normalize, url_in_scope

MAX_BYTES = 20 * 1024 * 1024
RenderJs = Callable[[str], Awaitable[str | None]]


@dataclass
class FetchOutcome:
    status: str
    http_status: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    title: str | None = None
    markdown: str | None = None
    content_hash: str | None = None
    links: list[str] | None = None
    error: str | None = None


def make_client(
    *,
    concurrency: int,
    transport: httpx.BaseTransport | None = None,
    trust_env: bool = True,
) -> httpx.AsyncClient:
    http2 = False
    try:
        import h2  # noqa: F401

        http2 = transport is None
    except ImportError:
        http2 = False
    return httpx.AsyncClient(
        http2=http2,
        follow_redirects=True,
        trust_env=trust_env,
        transport=transport,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        },
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(
            max_connections=max(concurrency, 1),
            max_keepalive_connections=max(concurrency, 1),
        ),
    )


async def fetch_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    seed_url: str,
    allow_subdomains: bool,
    etag: str | None = None,
    last_modified: str | None = None,
    render_js: RenderJs | None = None,
) -> FetchOutcome:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with client.stream("GET", url, headers=headers) as response:
            status = response.status_code
            content_type = response.headers.get("content-type")
            response_etag = response.headers.get("etag")
            response_modified = response.headers.get("last-modified")
            final = str(response.url)
            if status == 304:
                return FetchOutcome(
                    status="not_modified",
                    http_status=304,
                    content_type=content_type,
                    etag=response_etag or etag,
                    last_modified=response_modified or last_modified,
                    final_url=final,
                )
            if status in {401, 403}:
                await response.aread()
                return FetchOutcome(
                    status="blocked_auth",
                    http_status=status,
                    content_type=content_type,
                    final_url=final,
                    error=f"HTTP {status}",
                )
            if status < 200 or status >= 300:
                await response.aread()
                return FetchOutcome(
                    status="failed",
                    http_status=status,
                    content_type=content_type,
                    final_url=final,
                    error=f"HTTP {status}",
                )
            body = await _read_limited(response)
    except httpx.HTTPError as exc:
        return FetchOutcome(status="failed", error=f"{type(exc).__name__}: {exc}")

    if body is None:
        return FetchOutcome(
            status="failed",
            http_status=status,
            content_type=content_type,
            final_url=final,
            error=f"response larger than {MAX_BYTES} bytes",
        )

    final_normalized = try_normalize(final)
    if final_normalized is None or not url_in_scope(final_normalized, seed_url, allow_subdomains):
        landed = final_normalized or final
        return FetchOutcome(
            status="out_of_scope",
            http_status=status,
            content_type=content_type,
            final_url=final,
            error=f"redirected outside the crawl scope: {landed}",
        )

    if _is_pdf(url, content_type or "", body):
        try:
            markdown = extract_pdf(body)
        except ValueError as exc:
            return FetchOutcome(
                status="failed",
                http_status=status,
                content_type=content_type,
                etag=response_etag,
                last_modified=response_modified,
                final_url=final,
                error=str(exc),
            )
        title = None
        links: list[str] = []
    elif _is_html(content_type or "", body):
        extracted = extract_html(_decode_html(body, content_type), final_normalized)
        markdown = extracted.markdown
        title = extracted.title
        links = extracted.links
        if len(markdown) < MIN_USEFUL_CHARS and render_js is not None:
            rendered = await render_js(url)
            if rendered:
                extracted = extract_html(rendered, final_normalized)
                if len(extracted.markdown) >= len(markdown):
                    markdown = extracted.markdown
                    title = extracted.title or title
                    links = extracted.links or links
            elif len(markdown) < MIN_USEFUL_CHARS:
                short = _outcome(
                    "empty",
                    status,
                    content_type,
                    response_etag,
                    response_modified,
                    final,
                    title,
                    markdown,
                    links,
                )
                short.error = "js fallback produced no HTML"
                return short
    else:
        return FetchOutcome(
            status="failed",
            http_status=status,
            content_type=content_type,
            etag=response_etag,
            last_modified=response_modified,
            final_url=final,
            error=f"unsupported content type: {content_type or 'unknown'}",
        )

    page_status = "empty" if len(markdown) < MIN_USEFUL_CHARS else "fetched"
    return _outcome(
        page_status,
        status,
        content_type,
        response_etag,
        response_modified,
        final,
        title,
        markdown,
        links,
    )


def _outcome(
    status: str,
    http_status: int,
    content_type: str | None,
    etag: str | None,
    last_modified: str | None,
    final_url: str,
    title: str | None,
    markdown: str,
    links: list[str],
) -> FetchOutcome:
    normalized = normalize_markdown(markdown)
    return FetchOutcome(
        status=status,
        http_status=http_status,
        content_type=content_type,
        etag=etag,
        last_modified=last_modified,
        final_url=final_url,
        title=title,
        markdown=normalized,
        content_hash=content_hash(normalized),
        links=links,
    )


async def _read_limited(response: httpx.Response) -> bytes | None:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        return None
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _is_pdf(url: str, content_type: str, data: bytes) -> bool:
    if "pdf" in content_type.lower():
        return True
    path = normalize_url(url).lower() if try_normalize(url) else url.lower()
    if path.split("?", 1)[0].endswith(".pdf"):
        return True
    return data[:5] == b"%PDF-"


def _is_html(content_type: str, data: bytes) -> bool:
    lowered = content_type.lower()
    if "html" in lowered or "xml" in lowered or lowered.startswith("text/plain"):
        return True
    if not lowered or lowered.startswith("application/octet-stream"):
        sample = data.lstrip()[:64].lower()
        return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or sample.startswith(b"<head")
    return False


def conditional_validators(page) -> tuple[str | None, str | None]:
    if page is None or page.status not in {"fetched", "digest_ok", "digest_error", "empty"}:
        return None, None
    if not page.raw_path:
        return None, None
    return page.etag, page.last_modified


def request_host(url: str) -> str:
    return host_of(url)


def _decode_html(data: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip().strip("\"'")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")
