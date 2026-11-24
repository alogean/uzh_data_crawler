"""Summary stage: one local model call per document, no taxonomy involved."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from uzh_crawler.config import RunConfig
from uzh_crawler.llm.ollama import ModelOutputError, OllamaChat
from uzh_crawler.progress import Progress
from uzh_crawler.scrape.extract import MIN_USEFUL_CHARS
from uzh_crawler.store.cache_sqlite import StageCache
from uzh_crawler.store.crawl_json import SUMMARIZED_STATUSES, CrawlManifest, Page, save_manifest
from uzh_crawler.store.paths import safe_join, summary_relative
from uzh_crawler.summarize.prompt import SUMMARY_HEADINGS, system_prompt, user_prompt
from uzh_crawler.text import content_hash, normalize_markdown

logger = logging.getLogger(__name__)

_HEADING = re.compile(
    r"^##\s+(?:" + "|".join(SUMMARY_HEADINGS.values()) + r")\s*",
    re.IGNORECASE,
)


class PageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    resume_markdown: str = Field(min_length=1)


class OllamaSummarizer:
    def __init__(self, chat: OllamaChat, *, model: str):
        self.chat = chat
        self.model = model

    async def prepare(self) -> None:
        await self.chat.prepare()

    async def summarize(self, *, url: str, markdown: str, lang: str) -> PageSummary:
        data = await self.chat.json_object(
            model=self.model,
            system=system_prompt(lang),
            user=user_prompt(url, markdown),
            schema=PageSummary.model_json_schema(),
        )
        try:
            return PageSummary.model_validate(data)
        except ValidationError as exc:
            raise ModelOutputError(str(exc)) from exc


def needs_summary(page: Page, cache: StageCache, config: RunConfig, run_dir: Path) -> bool:
    if not page.raw_path or not page.content_hash:
        return False
    if page.status in {"fetched", "summary_error"}:
        return True
    if page.status not in SUMMARIZED_STATUSES:
        return False
    return not cache.summary_reusable(
        source=page.normalized_url,
        content_hash=page.content_hash,
        model=config.summary_model,
        lang=config.lang,
        run_dir=run_dir,
    )


async def summarize_pages(
    config: RunConfig,
    manifest: CrawlManifest,
    cache: StageCache,
    summarizer,
    pages: list[Page],
    progress: Progress | None = None,
) -> None:
    semaphore = asyncio.Semaphore(max(config.llm_concurrency, 1))
    manifest_path = config.out_dir / "crawl.json"

    async def one(page: Page) -> None:
        async with semaphore:
            status = await _summarize_page(config, cache, summarizer, page, manifest, progress)
            save_manifest(manifest_path, manifest)
            logger.debug("%s %s", status, page.normalized_url)
            if progress is not None:
                progress.tick(status, page.normalized_url)

    await asyncio.gather(*(one(page) for page in pages))


async def _summarize_page(config, cache, summarizer, page: Page, manifest, progress) -> str:
    # The old summary and its tags go first, so a crash cannot leave tags that
    # describe a summary another model wrote.
    page.status = "fetched"
    page.clear_summary()
    page.error = None
    save_manifest(config.out_dir / "crawl.json", manifest)
    try:
        raw_path = safe_join(config.out_dir, page.raw_path)
        markdown = normalize_markdown(raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        page.status = "summary_error"
        page.error = str(exc)[:500]
        return "erreur résumé"
    page.content_hash = content_hash(markdown)
    if len(markdown) < MIN_USEFUL_CHARS:
        page.status = "empty"
        return "vide"

    if progress is not None:
        progress.note(f"modèle en cours · {page.normalized_url}")
    summary = None
    error: Exception | None = None
    for _attempt in range(2):
        try:
            summary = await summarizer.summarize(
                url=page.normalized_url, markdown=markdown, lang=config.lang
            )
            break
        except (ModelOutputError, httpx.HTTPError, json.JSONDecodeError) as exc:
            error = exc
    if summary is None:
        page.status = "summary_error"
        page.error = str(error)[:500]
        return "erreur résumé"

    title = summary.title.strip() or page.title or page.normalized_url
    document = render_summary(
        title=title,
        source=page.normalized_url,
        content_hash=page.content_hash,
        model=config.summary_model,
        lang=config.lang,
        resume=summary.resume_markdown,
    )
    relative = summary_relative(page.normalized_url)
    target = safe_join(config.out_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8", newline="\n")
    page.summary_path = relative
    page.summary_hash = summary_hash(document)
    page.title = title
    page.status = "summarized"
    cache.put_summary(
        source=page.normalized_url,
        content_hash=page.content_hash,
        model=config.summary_model,
        lang=config.lang,
        output_file=relative,
        summary_hash=page.summary_hash,
    )
    return "résumé"


def render_summary(
    *,
    title: str,
    source: str,
    content_hash: str,
    model: str,
    lang: str,
    resume: str,
) -> str:
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"source: {json.dumps(source, ensure_ascii=False)}",
        f"content_hash: {json.dumps(content_hash, ensure_ascii=False)}",
        f"model: {json.dumps(model, ensure_ascii=False)}",
        f"lang: {lang}",
        "---",
        "",
        f"## {SUMMARY_HEADINGS[lang]}",
        "",
        summary_body(resume),
        "",
    ]
    return "\n".join(lines)


def summary_body(text: str) -> str:
    """The summary text without its leading "## Summary" heading."""
    return _HEADING.sub("", text.strip()).strip()


def summary_hash(document: str) -> str:
    return content_hash(normalize_markdown(document))


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("file is missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed")
    meta = yaml.safe_load(text[4:end])
    if not isinstance(meta, dict):
        raise ValueError("frontmatter must be a mapping")
    return meta, text[end + 5 :]
