"""Title and links of an HTML page, read with the lexbor HTML5 parser.

Discovery reads every page only for its links, so this is the hot path of a
crawl. lexbor parses a page about fifteen times faster than BeautifulSoup and
still follows the HTML5 rules for malformed markup.
"""

from __future__ import annotations

from urllib.parse import urljoin

from uzh_crawler.urls import try_normalize

_SKIP_HREF_PREFIXES = ("#", "mailto:", "tel:", "javascript:")
# Unrendered client-side templates, such as href="[%=href%]".
_TEMPLATE_MARKERS = ("[%", "{{", "${", "<%")


def extract_links(html: str, page_url: str) -> tuple[str | None, list[str]]:
    """Return the page title and every absolute, normalized ``a[href]``, in document order."""
    from selectolax.lexbor import LexborHTMLParser

    return links_of(LexborHTMLParser(html), page_url)


def links_of(tree, page_url: str) -> tuple[str | None, list[str]]:
    """The same, from a page that is already parsed."""
    base = tree.css_first("base[href]")
    base_href = base.attributes.get("href") if base is not None else None
    base_url = urljoin(page_url, base_href) if base_href else page_url
    title_node = tree.css_first("title")
    title = " ".join(title_node.text().split()) if title_node is not None else None
    found: dict[str, None] = {}
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.lower().startswith(_SKIP_HREF_PREFIXES):
            continue
        if any(marker in href for marker in _TEMPLATE_MARKERS):
            continue
        normalized = try_normalize(urljoin(base_url, href))
        if normalized:
            found.setdefault(normalized)
    return title or None, list(found)
