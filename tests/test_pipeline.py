import asyncio
import json
from pathlib import Path

import httpx
import office_files

from uzh_crawler.classify.schema import annotation_model
from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.pipeline import execute
from uzh_crawler.scrape import scraper
from uzh_crawler.scrape.fetch import make_client
from uzh_crawler.summarize.summarizer import PageSummary
from uzh_crawler.taxonomy import TagValidationError, load_taxonomy
from uzh_crawler.validate import validate_run

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"
SENTENCE = "La faculté de philosophie décrit les cursus de bachelor et de master. "
HOME = "https://www.phil.uzh.ch/"
STUDY = "https://www.phil.uzh.ch/de/studium.html"


def _html(body: str, links: list[str]) -> str:
    anchors = "".join(f'<a href="{href}">lien</a>' for href in links)
    return (
        "<!doctype html><html><head><title>Studium</title></head><body>"
        f"<nav>NAVSECRET</nav><main><h1>Studium</h1><p>{body}</p>{anchors}</main>"
        "</body></html>"
    )


class FakeSummarizer:
    def __init__(self):
        self.calls: list[str] = []

    async def summarize(self, *, url: str, markdown: str, lang: str):
        assert "<html" not in markdown.lower()
        assert "NAVSECRET" not in markdown
        assert lang == "fr"
        self.calls.append(url)
        return PageSummary(title="Studium", resume_markdown=f"## Résumé\n\nRésumé de {url}")


class FakeClassifier:
    def __init__(self, taxonomy, missing_unit=None):
        self.taxonomy = taxonomy
        self.missing_unit = missing_unit
        self.calls: list[str] = []
        self.seen: list[str] = []

    async def classify(self, *, url: str, title: str, summary: str, lang: str):
        self.calls.append(url)
        self.seen.append(summary)
        return annotation_model(self.taxonomy).model_validate(
            {
                "organisation": "philosophische",
                "publics": ["studierende"],
                "types_document": "portail",
                "offre_etudes": ["bachelor"],
                "unite_manquante": self.missing_unit,
            }
        )


class FailingClassifier:
    def __init__(self):
        self.calls = 0

    async def classify(self, *, url: str, title: str, summary: str, lang: str):
        self.calls += 1
        raise TagValidationError("sortie hors schéma")


def _site(requested: list[tuple[str, str | None]]):
    home = _html(
        SENTENCE * 6,
        [
            "/de/studium.html",
            "/dam/reglement.pdf",
            "https://www.uzh.ch/de.html",
            "https://example.com/no",
            "/secret",
            "/hidden",
        ],
    )
    study = _html(SENTENCE * 6, [])

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.url.path, request.headers.get("if-none-match")))
        assert request.headers["user-agent"] == USER_AGENT
        path = request.url.path
        matcher = request.headers.get("if-none-match")
        if path == "/robots.txt":
            body = "User-agent: *\nDisallow: /hidden\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        if path == "/secret":
            return httpx.Response(401, text="login")
        if path == "/":
            if matcher == '"home"':
                return httpx.Response(304, headers={"etag": '"home"'})
            return httpx.Response(
                200, text=home, headers={"content-type": "text/html; charset=utf-8", "etag": '"home"'}
            )
        if path == "/de/studium.html":
            if matcher == '"studium"':
                return httpx.Response(304, headers={"etag": '"studium"'})
            return httpx.Response(
                200, text=study, headers={"content-type": "text/html; charset=utf-8", "etag": '"studium"'}
            )
        return httpx.Response(404, text="missing")

    return handler


def _pages(out: Path) -> dict[str, dict]:
    manifest = json.loads((out / "crawl.json").read_text(encoding="utf-8"))
    return {page["normalized_url"]: page for page in manifest["pages"]}


def test_crawl_lists_urls_without_scraping(tmp_path: Path):
    requested: list[tuple[str, str | None]] = []

    async def crawl():
        async with _client(httpx.MockTransport(_site(requested))) as client:
            return await execute(_config(tmp_path), mode="crawl", client=client)

    assert asyncio.run(crawl()) == 0
    pages = _pages(tmp_path)
    assert pages[HOME]["status"] == "discovered"
    assert pages[STUDY]["status"] == "discovered"
    assert pages[HOME]["raw_path"] is None
    # The HTML that had to be read for its links is kept for the scrape stage.
    assert (tmp_path / pages[HOME]["file_path"]).read_text(encoding="utf-8").startswith("<!doctype")
    # A linked document is listed and not downloaded.
    pdf = pages["https://www.phil.uzh.ch/dam/reglement.pdf"]
    assert pdf["status"] == "discovered" and pdf["file_path"] is None
    assert "/dam/reglement.pdf" not in {path for path, _ in requested}
    assert pages["https://www.uzh.ch/de.html"]["status"] == "out_of_scope"
    assert pages["https://example.com/no"]["status"] == "out_of_scope"
    assert pages["https://www.phil.uzh.ch/secret"]["status"] == "blocked_auth"
    hidden = pages["https://www.phil.uzh.ch/hidden"]
    assert (hidden["status"], hidden["error"]) == ("failed", "disallowed by robots.txt")
    assert "/hidden" not in {path for path, _ in requested}
    listed = (tmp_path / "urls.txt").read_text(encoding="utf-8").splitlines()
    assert HOME in listed and STUDY in listed
    assert "https://www.phil.uzh.ch/dam/reglement.pdf" in listed
    assert "https://example.com/no" not in listed
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").glob("*.md"))

    # The scrape stage reuses the HTML that discovery kept: no second download.
    requested.clear()

    async def scrape():
        async with _client(httpx.MockTransport(_site(requested))) as client:
            return await execute(RunConfig(out_dir=tmp_path, rate_limit=0), mode="scrape", client=client)

    asyncio.run(scrape())
    fetched = {path for path, _ in requested}
    assert "/" not in fetched and "/de/studium.html" not in fetched
    assert "/dam/reglement.pdf" in fetched
    pages = _pages(tmp_path)
    assert pages[HOME]["status"] == "fetched"
    assert "NAVSECRET" not in (tmp_path / pages[HOME]["raw_path"]).read_text(encoding="utf-8")


def test_run_then_rerun_calls_each_model_only_when_its_input_changed(tmp_path: Path):
    taxonomy = load_taxonomy(TAXONOMY)
    requested: list[tuple[str, str | None]] = []
    summarizer = FakeSummarizer()
    classifier = FakeClassifier(taxonomy)
    config = _config(tmp_path)

    async def run(mode: str = "run", current: RunConfig = config):
        async with _client(httpx.MockTransport(_site(requested))) as client:
            return await execute(
                current, mode=mode, client=client, summarizer=summarizer, classifier=classifier
            )

    # The 404 PDF is a real failure, so the run reports a partial result.
    assert asyncio.run(run()) == 2
    manifest = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    pages = _pages(tmp_path)
    assert manifest["schema_version"] == 2
    assert manifest["finished_at"]
    home = pages[HOME]
    assert home["status"] == "tagged" and pages[STUDY]["status"] == "tagged"
    assert home["tags"] == {
        "organisation": "philosophische",
        "publics": ["studierende"],
        "types_document": "portail",
        "offre_etudes": ["bachelor"],
    }
    assert manifest["stats"]["urls_tagged"] == 2
    assert sorted(summarizer.calls) == sorted([HOME, STUDY])
    assert sorted(classifier.calls) == sorted([HOME, STUDY])
    # The classifier is given the summary, not the page text.
    assert all(text.startswith("Résumé de ") for text in classifier.seen)
    summary = (tmp_path / home["summary_path"]).read_text(encoding="utf-8")
    assert "## Résumé" in summary and "tags:" not in summary
    digest = (tmp_path / home["digest_path"]).read_text(encoding="utf-8")
    assert "organisation: philosophische" in digest and "## Résumé" in digest
    assert validate_run(tmp_path, taxonomy) == []

    asyncio.run(run())
    assert len(summarizer.calls) == 2 and len(classifier.calls) == 2

    # A new classification model redoes the tags and leaves the summaries alone.
    config.classify_model = "other-model"
    asyncio.run(run())
    assert len(summarizer.calls) == 2 and len(classifier.calls) == 4
    assert 'model: "other-model"' in (tmp_path / home["digest_path"]).read_text(encoding="utf-8")
    assert validate_run(tmp_path, taxonomy) == []

    # A new summary language redoes the summaries, and so the tags made from them.
    asyncio.run(
        run("summarize", RunConfig(out_dir=tmp_path, summary_model=config.summary_model, lang="fr"))
    )
    assert len(summarizer.calls) == 2
    config.summary_model = "other-summary-model"
    asyncio.run(run())
    assert len(summarizer.calls) == 4 and len(classifier.calls) == 6

    updated = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    for page in updated["pages"]:
        if page["normalized_url"] == HOME:
            page["tags"]["organisation"] = "invente"
    (tmp_path / "crawl.json").write_text(json.dumps(updated), encoding="utf-8")
    assert any("invente" in error for error in validate_run(tmp_path, taxonomy))


def test_http_failure_exits_with_partial_status(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/boom":
            return httpx.Response(500, text="down")
        return httpx.Response(
            200, text=_html(SENTENCE * 6, ["/boom"]), headers={"content-type": "text/html"}
        )

    async def run():
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(_config(tmp_path, max_pages=5), mode="crawl", client=client)

    assert asyncio.run(run()) == 2
    assert _pages(tmp_path)["https://www.phil.uzh.ch/boom"]["status"] == "failed"


def test_tag_error_is_retried_once_then_recorded(tmp_path: Path):
    classifier = FailingClassifier()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=_html(SENTENCE * 6, []), headers={"content-type": "text/html"})

    async def run():
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(
                _config(tmp_path, max_pages=1),
                mode="run",
                client=client,
                summarizer=FakeSummarizer(),
                classifier=classifier,
            )

    assert asyncio.run(run()) == 2
    assert classifier.calls == 2
    seed = _pages(tmp_path)[HOME]
    assert seed["status"] == "tag_error"
    assert seed["tags"] is None
    assert seed["summary_path"]
    assert "hors schéma" in seed["error"]


def test_sitemap_and_prefixes_keep_the_crawl_inside_the_section(tmp_path: Path):
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nSitemap: https://www.oec.uzh.ch/sitemap.txt\nAllow: /\n",
                headers={"content-type": "text/plain"},
            )
        if path == "/sitemap.txt":
            body = "\n".join(
                [
                    "https://www.oec.uzh.ch/de/studies/bachelor.html",
                    "https://www.oec.uzh.ch/de/faculty.html",
                    "https://www.oec.uzh.ch/de/studies/intranet/secret.html",
                ]
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/plain"})
        if path == "/de/studies.html":
            html = _html(SENTENCE * 6, ["/de/studies/master.html", "/de/about.html"])
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if path in {"/de/studies/bachelor.html", "/de/studies/master.html"}:
            return httpx.Response(200, text=_html(SENTENCE * 6, []), headers={"content-type": "text/html"})
        return httpx.Response(404, text="missing")

    def config(out: Path, **extra) -> RunConfig:
        return RunConfig(
            out_dir=out,
            seed_url="https://www.oec.uzh.ch/de/studies.html",
            max_pages=20,
            crawl_concurrency=2,
            rate_limit=0,
            include_prefixes={"oec.uzh.ch": ("/de/studies.html", "/de/studies")},
            exclude_path_contains=("/intranet",),
            **extra,
        )

    async def run(current: RunConfig):
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(current, mode="crawl", client=client)

    assert asyncio.run(run(config(tmp_path / "links"))) == 0
    pages = {url: page["status"] for url, page in _pages(tmp_path / "links").items()}
    assert pages["https://www.oec.uzh.ch/de/studies.html"] == "discovered"
    assert pages["https://www.oec.uzh.ch/de/studies/bachelor.html"] == "discovered"
    assert pages["https://www.oec.uzh.ch/de/studies/master.html"] == "discovered"
    assert "https://www.oec.uzh.ch/de/faculty.html" not in pages
    assert "https://www.oec.uzh.ch/de/about.html" not in pages
    assert "https://www.oec.uzh.ch/de/studies/intranet/secret.html" not in pages
    assert "/de/faculty.html" not in fetched and "/de/about.html" not in fetched

    # Sitemap only: the list comes from the sitemap and no page is read.
    fetched.clear()
    assert asyncio.run(run(config(tmp_path / "fast", sitemap_only=True))) == 0
    fast = _pages(tmp_path / "fast")
    assert set(fast) == {
        "https://www.oec.uzh.ch/de/studies.html",
        "https://www.oec.uzh.ch/de/studies/bachelor.html",
    }
    assert set(fetched) <= {"/robots.txt", "/sitemap.txt", "/sitemap.xml", "/sitemap_index.xml"}


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_scrape_urls_stores_each_original_and_its_markdown(tmp_path: Path):
    word = office_files.docx(
        office_files.paragraph("Antrag", style="Titel"), office_files.paragraph(SENTENCE * 6)
    )
    excel = office_files.xlsx({"Noten": [["Modul", "ECTS"]] + [[f"Modul {n} der Fakultät", 6] for n in range(20)]})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path.endswith("antrag.docx"):
            return httpx.Response(200, content=word, headers={"content-type": DOCX_TYPE})
        if path.endswith("kaputt.docx"):
            return httpx.Response(200, content=b"PK\x03\x04" + b"\x00" * 600, headers={"content-type": DOCX_TYPE})
        if path.endswith(".xlsx"):
            return httpx.Response(200, content=excel, headers={"content-type": XLSX_TYPE})
        if path.endswith(".pptx"):
            return httpx.Response(200, content=b"PK\x03\x04", headers={"content-type": "application/vnd.ms-powerpoint"})
        return httpx.Response(200, text=_html(SENTENCE * 6, ["/x"]), headers={"content-type": "text/html"})

    base = "https://www.phil.uzh.ch"
    urls = tuple(
        f"{base}{path}"
        for path in ("/de/studium.html", "/dam/antrag.docx", "/dam/noten.xlsx", "/dam/kaputt.docx", "/dam/folien.pptx")
    )

    async def run():
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(
                RunConfig(out_dir=tmp_path, rate_limit=0, max_pages=None),
                mode="scrape",
                urls=urls,
                client=client,
            )

    assert asyncio.run(run()) == 2  # one file could not be read
    pages = _pages(tmp_path)

    def text(page: dict) -> str:
        return (tmp_path / page["raw_path"]).read_text(encoding="utf-8")

    page = pages[urls[0]]
    assert page["status"] == "fetched"
    assert page["file_path"].endswith(".html") and (tmp_path / page["file_path"]).is_file()
    assert "bachelor" in text(page)
    docx = pages[urls[1]]
    assert docx["status"] == "fetched" and docx["file_path"].endswith(".docx")
    assert (tmp_path / docx["file_path"]).read_bytes() == word
    assert text(docx).startswith("# Antrag\n\nLa faculté")
    xlsx = pages[urls[2]]
    assert xlsx["status"] == "fetched" and xlsx["file_path"].endswith(".xlsx")
    assert "| Modul 3 der Fakultät | 6 |" in text(xlsx)
    broken = pages[urls[3]]
    assert broken["status"] == "failed" and "Word file could not be opened" in broken["error"]
    assert (tmp_path / broken["file_path"]).read_bytes().startswith(b"PK")
    slides = pages[urls[4]]
    assert slides["status"] == "empty" and slides["file_path"].endswith(".pptx")
    assert "no text extractor" in slides["error"]
    assert text(slides) == ""


def test_scrape_after_crawl_converts_stored_pages_in_worker_processes(tmp_path: Path):
    paths = [f"/de/seite-{n}.html" for n in range(9)]
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requested.append(path)
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(200, text=_html(SENTENCE * 6, paths), headers={"content-type": "text/html"})
        if path in paths:
            body = _html(f"Seite {path}. " + SENTENCE * 6, [])
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return httpx.Response(404)

    config = RunConfig(
        out_dir=tmp_path, seed_url=HOME, rate_limit=0, max_pages=None, discover_sitemaps=False, extract_workers=2
    )

    async def run(mode: str):
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(config, mode=mode, client=client)

    assert asyncio.run(run("crawl")) == 0
    requested.clear()
    assert asyncio.run(run("scrape")) == 0
    # Every page was read from the copy the crawl stored: no request, not even robots.txt.
    assert requested == []
    pages = _pages(tmp_path)
    assert len(pages) == 10
    for path in paths:
        page = pages[f"https://www.phil.uzh.ch{path}"]
        assert page["status"] == "fetched" and page["title"] == "Studium"
        markdown = (tmp_path / page["raw_path"]).read_text(encoding="utf-8")
        assert f"Seite {path}." in markdown and "NAVSECRET" not in markdown
        assert page["content_hash"].startswith("sha256:")


def test_js_fallback_replaces_a_page_whose_html_holds_too_little_text(tmp_path: Path, monkeypatch):
    short = "<html><head><title>Court</title></head><body><main><p>court</p></main></body></html>"
    long = "<html><head><title>Long</title></head><body><main><p>" + ("texte utile " * 40) + "</p></main></body></html>"
    rendered = {"html": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=short, headers={"content-type": "text/html"})

    async def render(_url: str):
        return rendered["html"]

    monkeypatch.setattr(scraper, "render_page", render)
    url = "https://www.uzh.ch/de.html"

    async def run(js_fallback: bool):
        async with _client(httpx.MockTransport(handler)) as client:
            config = RunConfig(out_dir=tmp_path, rate_limit=0, max_pages=None, js_fallback=js_fallback, refresh=True)
            return await execute(config, mode="scrape", urls=(url,), client=client)

    assert asyncio.run(run(False)) == 0
    page = _pages(tmp_path)[url]
    assert (page["status"], page["title"], page["error"]) == ("empty", "Court", None)

    assert asyncio.run(run(True)) == 0
    page = _pages(tmp_path)[url]
    assert (page["status"], page["error"]) == ("empty", "js fallback produced no HTML")

    rendered["html"] = long
    assert asyncio.run(run(True)) == 0
    page = _pages(tmp_path)[url]
    assert (page["status"], page["title"]) == ("fetched", "Long")
    assert "texte utile" in (tmp_path / page["raw_path"]).read_text(encoding="utf-8")


def test_version_1_run_keeps_its_crawl_and_redoes_the_model_work(tmp_path: Path):
    from uzh_crawler.store.crawl_json import load_manifest

    page = {"discovered_from": None, "depth": 0, "raw_path": "raw/a.md", "content_hash": "sha256:a"}
    manifest = {
        "schema_version": 1,
        "kind": "crawl_run",
        "run_id": "old",
        "seed_url": HOME,
        "started_at": "2026-11-22T19:00:00+01:00",
        "config": {"seeds": [HOME], "model": "qwen3.5:9b"},
        "pages": [
            dict(page, url=HOME, normalized_url=HOME, status="digest_ok", content_type="text/html",
                 digest_path="digests/a.md", tags={"organisation": "philosophische"}),
            dict(page, url=STUDY, normalized_url=STUDY, status="digest_ok",
                 content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ],
    }
    (tmp_path / "crawl.json").write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_manifest(tmp_path / "crawl.json")
    assert loaded.config["summary_model"] == "qwen3.5:9b"
    html, office = loaded.pages
    assert (html.status, html.raw_path, html.tags, html.digest_path) == ("fetched", "raw/a.md", None, None)
    assert (office.status, office.raw_path) == ("discovered", None)


def _config(out: Path, *, max_pages: int = 10) -> RunConfig:
    return RunConfig(
        out_dir=out,
        seed_url=HOME,
        taxonomy_path=TAXONOMY,
        summary_model="fake-test",
        classify_model="fake-test",
        max_pages=max_pages,
        crawl_concurrency=4,
        llm_concurrency=2,
        rate_limit=0,
        lang="fr",
    )


def _client(transport: httpx.BaseTransport) -> httpx.AsyncClient:
    return make_client(concurrency=4, transport=transport, trust_env=False)
