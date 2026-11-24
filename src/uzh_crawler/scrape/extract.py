from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown

from uzh_crawler.text import normalize_markdown
from uzh_crawler.urls import try_normalize

MIN_USEFUL_CHARS = 200
_DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "iframe")
_SKIP_HREF_PREFIXES = ("#", "mailto:", "tel:", "javascript:")
# Unrendered client-side templates, such as href="[%=href%]".
_TEMPLATE_MARKERS = ("[%", "{{", "${", "<%")


@dataclass(frozen=True)
class ExtractedHtml:
    title: str | None
    markdown: str
    links: list[str]


def extract_html(html: str, page_url: str) -> ExtractedHtml:
    """Turn a fetched document into Markdown plus every absolute a[href].

    Navigation is kept for discovery and removed from the text sent to the LLM.
    """
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base_href = base_tag.get("href") if base_tag else None
    base_url = urljoin(page_url, str(base_href)) if base_href else page_url
    links = _links(soup, base_url)
    title = None
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
    root = soup.find("main") or soup.find("article")
    if root is None:
        _drop(soup)
        root = soup.body or soup
    else:
        _drop(root)
    markdown = normalize_markdown(to_markdown(str(root), heading_style="ATX", bullets="-"))
    return ExtractedHtml(title=title, markdown=markdown, links=links)


def extract_links(html: str, page_url: str) -> tuple[str | None, list[str]]:
    """Title and links only. Discovery uses this and skips the Markdown conversion."""
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base_href = base_tag.get("href") if base_tag else None
    base_url = urljoin(page_url, str(base_href)) if base_href else page_url
    title = None
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
    return title, _links(soup, base_url)


def extract_pdf(data: bytes) -> str:
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - package is a required dependency
        import fitz as pymupdf
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF could not be opened: {exc}") from exc
    parts: list[str] = []
    try:
        for page in document:
            text = _page_text(page)
            if text:
                parts.append(text)
    finally:
        document.close()
    return normalize_markdown("\n\n".join(parts))


def _page_text(page: object) -> str:
    getter = getattr(page, "get_text")
    try:
        markdown = getter("markdown")
    except Exception:
        markdown = ""
    if isinstance(markdown, str) and markdown.strip():
        return markdown
    plain = getter("text")
    return plain if isinstance(plain, str) else ""


def _drop(root) -> None:
    for tag in root.find_all(_DROP_TAGS):
        tag.decompose()


def _links(soup: BeautifulSoup, base_url: str) -> list[str]:
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if isinstance(href, list):
            href = href[0] if href else ""
        href = str(href or "").strip()
        if not href or href.lower().startswith(_SKIP_HREF_PREFIXES):
            continue
        if any(marker in href for marker in _TEMPLATE_MARKERS):
            continue
        normalized = try_normalize(urljoin(base_url, href))
        if normalized and normalized not in found:
            found.append(normalized)
    return found
