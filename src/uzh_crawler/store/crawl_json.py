from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

PageStatus = Literal[
    "fetched",
    "digest_ok",
    "digest_error",
    "empty",
    "blocked_auth",
    "out_of_scope",
    "failed",
]

PAGE_STATUSES = set(get_args(PageStatus))
ERROR_STATUSES = {"failed", "digest_error"}
FETCHED_OK_STATUSES = {"fetched", "digest_ok", "digest_error", "empty"}


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
    raw_path: str | None = None
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    title: str | None = None
    digest_path: str | None = None
    tags: dict | None = None
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
            "raw_path": self.raw_path,
            "content_hash": self.content_hash,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "status": self.status,
            "title": self.title,
            "digest_path": self.digest_path,
            "tags": self.tags,
            "error": self.error,
        }


def page_from_dict(data: dict) -> Page:
    status = data.get("status")
    if status not in PAGE_STATUSES:
        raise ValueError(f"unknown page status: {status}")
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
    return Page(
        url=url,
        normalized_url=normalized,
        discovered_from=data.get("discovered_from"),
        depth=depth,
        status=status,
        http_status=data.get("http_status"),
        content_type=data.get("content_type"),
        fetched_at=data.get("fetched_at"),
        raw_path=data.get("raw_path"),
        content_hash=data.get("content_hash"),
        etag=data.get("etag"),
        last_modified=data.get("last_modified"),
        title=data.get("title"),
        digest_path=data.get("digest_path"),
        tags=tags,
        error=data.get("error"),
    )


@dataclass
class CrawlManifest:
    run_id: str
    seed_url: str
    started_at: str
    config: dict
    finished_at: str | None = None
    pages: list[Page] = field(default_factory=list)
    schema_version: int = 1
    kind: str = "crawl_run"

    def page(self, normalized_url: str) -> Page | None:
        for current in self.pages:
            if current.normalized_url == normalized_url:
                return current
        return None

    def upsert(self, page: Page) -> None:
        for index, current in enumerate(self.pages):
            if current.normalized_url == page.normalized_url:
                self.pages[index] = page
                return
        self.pages.append(page)

    def stats(self) -> dict:
        return {
            "urls_discovered": len(self.pages),
            "urls_fetched_ok": sum(page.status in FETCHED_OK_STATUSES for page in self.pages),
            "urls_failed": sum(page.status == "failed" for page in self.pages),
            "urls_digested": sum(page.status == "digest_ok" for page in self.pages),
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
    if data.get("schema_version") != 1:
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
    return CrawlManifest(
        run_id=run_id,
        seed_url=seed,
        started_at=started,
        finished_at=data.get("finished_at"),
        config=config,
        pages=[page_from_dict(page) for page in pages_raw],
    )
