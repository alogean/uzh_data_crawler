import asyncio
from pathlib import Path

import pymupdf

from uzh_crawler.scrape.extract import extract_html, extract_pdf
from uzh_crawler.scrape.fetch import sniff_kind
from uzh_crawler.scrape.scraper import _extract

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "page.html"


def test_html_keeps_main_text_and_links_but_drops_chrome():
    extracted = extract_html(FIXTURE.read_text(encoding="utf-8"), "https://www.phil.uzh.ch/de/studium.html")
    assert extracted.title == "Studium an der Philosophischen Fakultät"
    assert "bachelor et de master" in extracted.markdown
    assert "Navigation secrète" not in extracted.markdown
    assert "ne-pas-extraire" not in extracted.markdown
    assert "Pied de page" not in extracted.markdown
    assert "Encart à ignorer" not in extracted.markdown
    assert "https://www.phil.uzh.ch/de/home.html" in extracted.links
    assert "https://www.uzh.ch/de.html" in extracted.links
    assert "https://www.phil.uzh.ch/de/studium/bachelor.html" in extracted.links
    assert "https://example.com/hors" in extracted.links
    assert "https://www.phil.uzh.ch/de/doc.pdf?id=7" in extracted.links
    assert all(not link.startswith("mailto:") for link in extracted.links)


def test_pdf_text_is_extracted():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Reglement de la faculte de lettres")
    data = document.tobytes()
    document.close()
    assert "Reglement" in extract_pdf(data)


def test_short_html_stays_empty_until_js_fallback_returns_text():
    short = b"<html><head><title>Court</title></head><body><main><p>court</p></main></body></html>"
    long = "<html><head><title>Long</title></head><body><main><p>" + ("texte utile " * 40) + "</p></main></body></html>"
    url = "https://www.uzh.ch/de.html"

    async def render(_url: str) -> str:
        return long

    empty = asyncio.run(_extract("html", short, "text/html", url, url, None))
    assert len(empty.markdown) < 200
    assert empty.links == []
    filled = asyncio.run(_extract("html", short, "text/html", url, url, render))
    assert "texte utile" in filled.markdown
    assert filled.title == "Long"


def test_the_media_type_decides_what_is_html():
    office = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert sniff_kind("https://www.uzh.ch/a.docx", office, b"PK\x03\x04") == "other"
    assert sniff_kind("https://www.uzh.ch/a", "text/html; charset=utf-8", b"") == "html"
    assert sniff_kind("https://www.uzh.ch/a", "application/pdf", b"") == "pdf"
    assert sniff_kind("https://www.uzh.ch/a", None, b"%PDF-1.7") == "pdf"
    assert sniff_kind("https://www.uzh.ch/a", "application/octet-stream", b"<!doctype html><html>") == "html"
    assert sniff_kind("https://www.uzh.ch/feed", "application/rss+xml", b"<rss>") == "other"
