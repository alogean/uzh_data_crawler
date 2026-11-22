import asyncio
import json
from pathlib import Path

import httpx

from uzh_crawler.config import USER_AGENT, RunConfig
from uzh_crawler.digest.schema import annotation_model
from uzh_crawler.pipeline import execute
from uzh_crawler.taxonomy import TagValidationError, load_taxonomy
from uzh_crawler.validate import validate_run

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"
SENTENCE = "La faculté de philosophie décrit les cursus de bachelor et de master. "


def _html(body: str, links: list[str]) -> str:
    anchors = "".join(f'<a href="{href}">lien</a>' for href in links)
    return (
        "<!doctype html><html><head><title>Studium</title></head><body>"
        f"<nav>NAVSECRET</nav><main><h1>Studium</h1><p>{body}</p>{anchors}</main>"
        "</body></html>"
    )


class FakeAnnotator:
    def __init__(self, taxonomy):
        self.taxonomy = taxonomy
        self.calls: list[str] = []

    async def annotate(self, *, url: str, markdown: str, lang: str):
        assert "<html" not in markdown.lower()
        assert "NAVSECRET" not in markdown
        assert lang == "fr"
        self.calls.append(url)
        return annotation_model(self.taxonomy).model_validate(
            {
                "organisation": "philosophische",
                "publics": ["studierende"],
                "types_document": "portail",
                "offre_etudes": ["bachelor"],
                "title": "Studium",
                "resume_markdown": f"Résumé de {url}",
            }
        )


class FailingAnnotator:
    def __init__(self):
        self.calls = 0

    async def annotate(self, *, url: str, markdown: str, lang: str):
        self.calls += 1
        raise TagValidationError("sortie hors schéma")


def test_crawl_digest_rerun_skips_llm_until_the_model_changes(tmp_path: Path):
    taxonomy = load_taxonomy(TAXONOMY)
    requested: list[tuple[str, str | None]] = []
    home = _html(
        SENTENCE * 6,
        [
            "/de/studium.html",
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
        if path == "/hidden":
            return httpx.Response(200, text="disallowed")
        if path == "/":
            if matcher == '"home"':
                return httpx.Response(304, headers={"etag": '"home"'})
            return httpx.Response(
                200,
                text=home,
                headers={"content-type": "text/html; charset=utf-8", "etag": '"home"'},
            )
        if path == "/de/studium.html":
            if matcher == '"studium"':
                return httpx.Response(304, headers={"etag": '"studium"'})
            return httpx.Response(
                200,
                text=study,
                headers={"content-type": "text/html; charset=utf-8", "etag": '"studium"'},
            )
        return httpx.Response(404, text="missing")

    annotator = FakeAnnotator(taxonomy)
    config = _config(tmp_path, model="fake-test")
    transport = httpx.MockTransport(handler)

    async def run(current: RunConfig):
        async with _client(transport) as client:
            return await execute(current, mode="run", client=client, annotator=annotator)

    code = asyncio.run(run(config))
    assert code == 0
    manifest = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    pages = {page["normalized_url"]: page for page in manifest["pages"]}
    assert manifest["kind"] == "crawl_run"
    assert manifest["schema_version"] == 1
    assert manifest["finished_at"]
    home_page = pages["https://www.phil.uzh.ch/"]
    study_page = pages["https://www.phil.uzh.ch/de/studium.html"]
    assert home_page["status"] == "digest_ok"
    assert study_page["status"] == "digest_ok"
    assert home_page["tags"]["organisation"] == "philosophische"
    assert home_page["tags"]["publics"] == ["studierende"]
    assert home_page["tags"]["offre_etudes"] == ["bachelor"]
    assert pages["https://www.uzh.ch/de.html"]["status"] == "out_of_scope"
    assert pages["https://example.com/no"]["status"] == "out_of_scope"
    assert pages["https://example.com/no"]["digest_path"] is None
    assert pages["https://www.phil.uzh.ch/secret"]["status"] == "blocked_auth"
    hidden = pages["https://www.phil.uzh.ch/hidden"]
    assert hidden["status"] == "failed"
    assert hidden["error"] == "disallowed by robots.txt"
    assert manifest["stats"]["urls_digested"] == 2
    assert set(annotator.calls) == {
        "https://www.phil.uzh.ch/",
        "https://www.phil.uzh.ch/de/studium.html",
    }
    assert len(annotator.calls) == 2
    digest = (tmp_path / home_page["digest_path"]).read_text(encoding="utf-8")
    assert digest.startswith("---\n")
    assert "organisation: philosophische" in digest
    assert "## Résumé" in digest
    assert "NAVSECRET" not in (tmp_path / home_page["raw_path"]).read_text(encoding="utf-8")
    assert validate_run(tmp_path, taxonomy) == []
    started = manifest["started_at"]
    assert "/hidden" not in {path for path, _ in requested}
    assert not any(path in {"/de.html", "/no"} for path, _ in requested)

    requested.clear()
    assert asyncio.run(run(config)) == 0
    assert annotator.calls and len(annotator.calls) == 2
    assert requested
    assert all(matcher for path, matcher in requested if path != "/robots.txt")
    assert not any(path == "/secret" for path, _ in requested)
    rerun = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    assert rerun["started_at"] == started

    config.model = "other-model"
    assert asyncio.run(run(config)) == 0
    assert len(annotator.calls) == 4
    updated = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    assert updated["config"]["model"] == "other-model"
    rewritten = (tmp_path / home_page["digest_path"]).read_text(encoding="utf-8")
    assert 'model: "other-model"' in rewritten
    assert validate_run(tmp_path, taxonomy) == []

    updated["pages"][0]["tags"]["organisation"] = "invente"
    (tmp_path / "crawl.json").write_text(json.dumps(updated), encoding="utf-8")
    errors = validate_run(tmp_path, taxonomy)
    assert errors
    assert any("invente" in error for error in errors)


def test_http_failure_exits_with_partial_status(tmp_path: Path):
    taxonomy = load_taxonomy(TAXONOMY)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/boom":
            return httpx.Response(500, text="down")
        return httpx.Response(
            200,
            text=_html(SENTENCE * 6, ["/boom"]),
            headers={"content-type": "text/html"},
        )

    annotator = FakeAnnotator(taxonomy)

    async def run():
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(
                _config(tmp_path, model="fake-test", max_pages=5),
                mode="run",
                client=client,
                annotator=annotator,
            )

    assert asyncio.run(run()) == 2
    manifest = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    failed = [page for page in manifest["pages"] if page["normalized_url"].endswith("/boom")]
    assert failed and failed[0]["status"] == "failed"
    assert all(not call.endswith("/boom") for call in annotator.calls)


def test_digest_error_is_retried_once_then_recorded(tmp_path: Path):
    taxonomy = load_taxonomy(TAXONOMY)
    annotator = FailingAnnotator()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(
            200,
            text=_html(SENTENCE * 6, []),
            headers={"content-type": "text/html"},
        )

    async def run():
        async with _client(httpx.MockTransport(handler)) as client:
            return await execute(
                _config(tmp_path, model="fake-test", max_pages=1),
                mode="run",
                client=client,
                annotator=annotator,
            )

    assert asyncio.run(run()) == 2
    assert annotator.calls == 2
    manifest = json.loads((tmp_path / "crawl.json").read_text(encoding="utf-8"))
    seed = manifest["pages"][0]
    assert seed["status"] == "digest_error"
    assert seed["tags"] is None
    assert "hors schéma" in seed["error"]


def _config(out: Path, *, model: str, max_pages: int = 10) -> RunConfig:
    return RunConfig(
        seed_url="https://www.phil.uzh.ch/",
        taxonomy_path=TAXONOMY,
        out_dir=out,
        model=model,
        max_pages=max_pages,
        crawl_concurrency=4,
        llm_concurrency=2,
        rate_limit=0,
        lang="fr",
    )


def _client(transport: httpx.BaseTransport) -> httpx.AsyncClient:
    from uzh_crawler.crawl.fetch import make_client

    return make_client(concurrency=4, transport=transport, trust_env=False)
