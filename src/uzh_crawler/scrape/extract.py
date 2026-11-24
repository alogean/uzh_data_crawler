from __future__ import annotations

from dataclasses import dataclass
from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown

from uzh_crawler.crawl.links import extract_links
from uzh_crawler.text import normalize_markdown

MIN_USEFUL_CHARS = 200
_DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "iframe")


@dataclass(frozen=True)
class ExtractedHtml:
    title: str | None
    markdown: str
    links: list[str]


def extract_html(html: str, page_url: str) -> ExtractedHtml:
    """Turn a fetched document into Markdown plus every absolute a[href].

    Navigation is kept for discovery and removed from the text sent to the LLM.
    """
    title, links = extract_links(html, page_url)
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.find("article")
    if root is None:
        _drop(soup)
        root = soup.body or soup
    else:
        _drop(root)
    markdown = normalize_markdown(to_markdown(str(root), heading_style="ATX", bullets="-"))
    return ExtractedHtml(title=title, markdown=markdown, links=links)


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
