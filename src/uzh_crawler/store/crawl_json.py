from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

SCHEMA_VERSION = 2

PageStatus = Literal[
    "discovered",
    "fetched",
    "empty",
    "summarized",
    "summary_error",
    "tagged",
    "tag_error",
    "blocked_auth",
    "out_of_scope",
    "failed",
]

PAGE_STATUSES = set(get_args(PageStatus))
ERROR_STATUSES = {"failed", "summary_error", "tag_error"}
# A page in one of these statuses has a scraped copy under raw/.
STORED_STATUSES = {"fetched", "empty", "summarized", "summary_error", "tagged", "tag_error"}
SUMMARIZED_STATUSES = {"summarized", "tagged", "tag_error"}
ROBOTS_ERROR = "disallowed by robots.txt"

_V1_STATUSES = {
    "fetched": "fetched",
    "digest_ok": "fetched",
    "digest_error": "fetched",
    "empty": "empty",
    "blocked_auth": "blocked_auth",
    "out_of_scope": "out_of_scope",
    "failed": "failed",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Page:
    url: str
    normalized_url: str
    discovered_from: str | None
    depth: int
    status: PageStatus
    http_status: int | None = None
    content_type: str | None = None
    fetched_at: str | None = None
    file_path: str | None = None
    raw_path: str | None = None
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    title: str | None = None
    summary_path: str | None = None
    summary_hash: str | None = None
    digest_path: str | None = None
    tags: dict | None = None
    missing_unit: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "discovered_from": self.discovered_from,
            "depth": self.depth,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "fetched_at": self.fetched_at,
            "file_path": self.file_path,
            "raw_path": self.raw_path,
            "content_hash": self.content_hash,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "status": self.status,
            "title": self.title,
            "summary_path": self.summary_path,
            "summary_hash": self.summary_hash,
            "digest_path": self.digest_path,
            "tags": self.tags,
            "missing_unit": self.missing_unit,
            "error": self.error,
        }

    def clear_summary(self) -> None:
        """Forget the summary and everything derived from it."""
        self.summary_path = None
        self.summary_hash = None
        self.clear_tags()

    def clear_tags(self) -> None:
        self.digest_path = None
        self.tags = None
        self.missing_unit = None

    def settled_status(self) -> PageStatus:
        """Status the stored fields support, used when the content is unchanged."""
        if self.tags and self.digest_path:
            return "tagged"
        if self.summary_path:
            return "summarized"
        return "fetched"


def page_from_dict(data: dict, *, schema_version: int = SCHEMA_VERSION) -> Page:
    status = data.get("status")
    url = data.get("url")
    normalized = data.get("normalized_url")
    if not isinstance(url, str) or not isinstance(normalized, str):
        raise ValueError("page url and normalized_url are required")
    depth = data.get("depth")
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise ValueError(f"page depth is required: {url}")
    tags = data.get("tags")
    if tags is not None and not isinstance(tags, dict):
        raise ValueError(f"page tags must be an object or null: {url}")
    page = Page(
        url=url,
        normalized_url=normalized,
        discovered_from=data.get("discovered_from"),
        depth=depth,
        status=status,
        http_status=data.get("http_status"),
        content_type=data.get("content_type"),
        fetched_at=data.get("fetched_at"),
        file_path=data.get("file_path"),
        raw_path=data.get("raw_path"),
        content_hash=data.get("content_hash"),
        etag=data.get("etag"),
        last_modified=data.get("last_modified"),
        title=data.get("title"),
        summary_path=data.get("summary_path"),
        summary_hash=data.get("summary_hash"),
        digest_path=data.get("digest_path"),
        tags=tags,
        missing_unit=data.get("missing_unit"),
        error=data.get("error"),
    )
    if schema_version == 1:
        _upgrade_v1_page(page)
    if page.status not in PAGE_STATUSES:
        raise ValueError(f"unknown page status: {status}")
    return page


def _upgrade_v1_page(page: Page) -> None:
    """Keep the crawl of a version 1 run; its summaries and tags are redone.

    Version 1 made the summary and the tags in one model call, so there is no
    summary to classify from. It also stored Office files as if they were HTML;
    those go back to ``discovered`` so the next scrape reads them properly.
    """
    page.status = _V1_STATUSES.get(page.status, page.status)
    page.clear_summary()
    ctype = (page.content_type or "").lower()
    readable = "html" in ctype or "pdf" in ctype or ctype.startswith("text/plain")
    if page.status in {"fetched", "empty"} and not readable:
        page.status = "discovered"
        page.raw_path = None
        page.content_hash = None
        page.etag = None
        page.last_modified = None


@dataclass
class CrawlManifest:
    run_id: str
    seed_url: str
    started_at: str
    config: dict
    finished_at: str | None = None
    pages: list[Page] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    kind: str = "crawl_run"
    _index: dict[str, int] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index = {page.normalized_url: i for i, page in enumerate(self.pages)}

    def page(self, normalized_url: str) -> Page | None:
        index = self._index.get(normalized_url)
        return None if index is None else self.pages[index]

    def upsert(self, page: Page) -> None:
        index = self._index.get(page.normalized_url)
        if index is None:
            self._index[page.normalized_url] = len(self.pages)
            self.pages.append(page)
        else:
            self.pages[index] = page

    def stats(self) -> dict:
        return {
            "urls_discovered": len(self.pages),
            "urls_fetched_ok": sum(page.status in STORED_STATUSES for page in self.pages),
            "urls_failed": sum(page.status == "failed" for page in self.pages),
            "urls_summarized": sum(page.status in SUMMARIZED_STATUSES for page in self.pages),
            "urls_tagged": sum(page.status == "tagged" for page in self.pages),
        }

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "run_id": self.run_id,
            "seed_url": self.seed_url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "config": self.config,
            "stats": self.stats(),
            "pages": [page.to_dict() for page in self.pages],
        }


def save_manifest(path: Path, manifest: CrawlManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)


def load_manifest(path: Path) -> CrawlManifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"crawl.json is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("kind") != "crawl_run":
        raise ValueError("crawl.json kind must be crawl_run")
    version = data.get("schema_version")
    if version not in {1, SCHEMA_VERSION}:
        raise ValueError("unsupported crawl.json schema_version")
    pages_raw = data.get("pages")
    if not isinstance(pages_raw, list):
        raise ValueError("crawl.json pages must be a list")
    config = data.get("config")
    if not isinstance(config, dict):
        raise ValueError("crawl.json config must be an object")
    seed = data.get("seed_url")
    run_id = data.get("run_id")
    started = data.get("started_at")
    if not all(isinstance(item, str) for item in (seed, run_id, started)):
        raise ValueError("crawl.json is missing run_id, seed_url, or started_at")
    if version == 1 and "model" in config:
        config = dict(config)
        model = config.pop("model")
        config.setdefault("summary_model", model)
        config.setdefault("classify_model", model)
    return CrawlManifest(
        run_id=run_id,
        seed_url=seed,
        started_at=started,
        finished_at=data.get("finished_at"),
        config=config,
        pages=[page_from_dict(page, schema_version=version) for page in pages_raw],
    )
