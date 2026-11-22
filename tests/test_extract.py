import asyncio
from pathlib import Path

import httpx
import pymupdf

from uzh_crawler.crawl.extract import extract_html, extract_pdf
from uzh_crawler.crawl.fetch import fetch_url, make_client

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
    short = "<html><head><title>Court</title></head><body><main><p>court</p></main></body></html>"
    long = "<html><head><title>Long</title></head><body><main><p>" + ("texte utile " * 40) + "</p></main></body></html>"

    async def scenario(render):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=short, headers={"content-type": "text/html"})
        )
        async with make_client(concurrency=1, transport=transport, trust_env=False) as client:
            return await fetch_url(
                client,
                "https://www.uzh.ch/de.html",
                seed_url="https://www.uzh.ch/de.html",
                allow_subdomains=False,
                render_js=render,
            )

    empty = asyncio.run(scenario(None))
    assert empty.status == "empty"
    assert empty.links == []

    async def render(_url: str) -> str:
        return long

    filled = asyncio.run(scenario(render))
    assert filled.status == "fetched"
    assert "texte utile" in (filled.markdown or "")
    assert filled.title == "Long"
