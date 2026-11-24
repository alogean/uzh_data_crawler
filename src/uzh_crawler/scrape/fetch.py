from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from uzh_crawler.config import USER_AGENT

MAX_BYTES = 20 * 1024 * 1024

# Linked documents. Discovery lists them without downloading them.
DOCUMENT_SUFFIXES = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".csv",
    ".epub",
)


@dataclass
class Download:
    """One HTTP exchange. ``kind`` is ok, not_modified, not_html, blocked_auth or failed."""

    kind: str
    http_status: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    body: bytes | None = None
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


async def download(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    html_only: bool = False,
) -> Download:
    """GET one URL. With ``html_only`` the body of a non-HTML response is not read."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with client.stream("GET", url, headers=headers) as response:
            status = response.status_code
            common = dict(
                http_status=status,
                content_type=response.headers.get("content-type"),
                final_url=str(response.url),
            )
            response_etag = response.headers.get("etag")
            response_modified = response.headers.get("last-modified")
            if status == 304:
                return Download(
                    kind="not_modified",
                    etag=response_etag or etag,
                    last_modified=response_modified or last_modified,
                    **common,
                )
            if status in {401, 403}:
                return Download(kind="blocked_auth", error=f"HTTP {status}", **common)
            if status < 200 or status >= 300:
                return Download(kind="failed", error=f"HTTP {status}", **common)
            if html_only and header_kind(url, common["content_type"]) not in {"html", "unknown"}:
                return Download(
                    kind="not_html",
                    etag=response_etag,
                    last_modified=response_modified,
                    **common,
                )
            body = await _read_limited(response)
    except httpx.HTTPError as exc:
        return Download(kind="failed", error=f"{type(exc).__name__}: {exc}")
    if body is None:
        return Download(
            kind="failed",
            error=f"response larger than {MAX_BYTES} bytes",
            **common,
        )
    return Download(
        kind="ok",
        etag=response_etag,
        last_modified=response_modified,
        body=body,
        **common,
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


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def header_kind(url: str, content_type: str | None) -> str:
    """What the headers alone say: html, pdf, text, other, or unknown."""
    media = media_type(content_type)
    if media in {"text/html", "application/xhtml+xml"}:
        return "html"
    if media == "application/pdf" or (not media and is_document_url(url, (".pdf",))):
        return "pdf"
    if media == "text/plain":
        return "text"
    if not media or media == "application/octet-stream":
        return "unknown"
    return "other"


def sniff_kind(url: str, content_type: str | None, body: bytes) -> str:
    """html, pdf, text or other, from the headers and then the first bytes.

    The media type is compared exactly. An Office file is
    ``application/vnd.openxmlformats-…``: it contains "xml" and is not HTML.
    """
    kind = header_kind(url, content_type)
    if kind != "unknown":
        return kind
    if body[:5] == b"%PDF-" or is_document_url(url, (".pdf",)):
        return "pdf"
    sample = body.lstrip()[:64].lower()
    if sample.startswith((b"<!doctype html", b"<html", b"<head")):
        return "html"
    return "other"


def is_document_url(url: str, suffixes: tuple[str, ...] = DOCUMENT_SUFFIXES) -> bool:
    return urlsplit(url).path.lower().endswith(suffixes)


def decode_text(data: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip().strip("\"'")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def conditional_validators(page) -> tuple[str | None, str | None]:
    """ETag and Last-Modified to send, when a stored copy can answer a 304."""
    if page is None or not (page.raw_path or page.file_path):
        return None, None
    return page.etag, page.last_modified
