"""Classification stage: tag a summary with ids from the taxonomy."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from pydantic import BaseModel

from uzh_crawler.classify.prompt import system_prompt, user_prompt
from uzh_crawler.classify.render import render_digest
from uzh_crawler.classify.schema import annotation_model, missing_unit_of, parse_annotation, tags_of
from uzh_crawler.config import RunConfig
from uzh_crawler.llm.ollama import ModelOutputError, OllamaChat
from uzh_crawler.progress import Progress
from uzh_crawler.store.cache_sqlite import StageCache
from uzh_crawler.store.crawl_json import CrawlManifest, Page, save_manifest
from uzh_crawler.store.paths import digest_relative, safe_join
from uzh_crawler.summarize.summarizer import parse_frontmatter, summary_body, summary_hash
from uzh_crawler.taxonomy import TagValidationError, Taxonomy

logger = logging.getLogger(__name__)


class OllamaClassifier:
    def __init__(self, chat: OllamaChat, taxonomy: Taxonomy, *, model: str):
        self.chat = chat
        self.taxonomy = taxonomy
        self.model = model

    async def prepare(self) -> None:
        await self.chat.prepare()

    async def classify(self, *, url: str, title: str, summary: str, lang: str) -> BaseModel:
        data = await self.chat.json_object(
            model=self.model,
            system=system_prompt(self.taxonomy, lang),
            user=user_prompt(url, title, summary),
            schema=annotation_model(self.taxonomy).model_json_schema(),
        )
        return parse_annotation(data, self.taxonomy)


def needs_classification(
    page: Page, cache: StageCache, config: RunConfig, taxonomy: Taxonomy, run_dir: Path
) -> bool:
    if not page.summary_path or not page.summary_hash:
        return False
    if page.status in {"summarized", "tag_error"}:
        return True
    if page.status != "tagged":
        return False
    return not cache.classification_reusable(
        source=page.normalized_url,
        summary_hash=page.summary_hash,
        model=config.classify_model,
        taxonomy_sha256=taxonomy.sha256,
        run_dir=run_dir,
    )


async def classify_pages(
    config: RunConfig,
    taxonomy: Taxonomy,
    manifest: CrawlManifest,
    cache: StageCache,
    classifier,
    pages: list[Page],
    progress: Progress | None = None,
) -> None:
    semaphore = asyncio.Semaphore(max(config.llm_concurrency, 1))
    manifest_path = config.out_dir / "crawl.json"

    async def one(page: Page) -> None:
        async with semaphore:
            status = await _classify_page(config, taxonomy, cache, classifier, page, manifest, progress)
            save_manifest(manifest_path, manifest)
            logger.debug("%s %s", status, page.normalized_url)
            if progress is not None:
                progress.tick(status, page.normalized_url)

    await asyncio.gather(*(one(page) for page in pages))


async def _classify_page(config, taxonomy, cache, classifier, page: Page, manifest, progress) -> str:
    # Drop the previous tags before the call so a crash cannot keep stale tags
    # under a new model or a new taxonomy.
    page.status = "summarized"
    page.clear_tags()
    page.error = None
    save_manifest(config.out_dir / "crawl.json", manifest)
    try:
        document = safe_join(config.out_dir, page.summary_path).read_text(encoding="utf-8")
        meta, body = parse_frontmatter(document)
    except (OSError, ValueError) as exc:
        page.status = "tag_error"
        page.error = str(exc)[:500]
        return "erreur tags"
    page.summary_hash = summary_hash(document)
    title = str(meta.get("title") or page.title or page.normalized_url)
    summary = summary_body(body)

    if progress is not None:
        progress.note(f"modèle en cours · {page.normalized_url}")
    annotation = None
    error: Exception | None = None
    for _attempt in range(2):
        try:
            annotation = await classifier.classify(
                url=page.normalized_url, title=title, summary=summary, lang=config.lang
            )
            tags = taxonomy.validate_tags(tags_of(annotation))
            break
        except (TagValidationError, ModelOutputError, httpx.HTTPError, json.JSONDecodeError) as exc:
            annotation = None
            error = exc
    if annotation is None:
        page.status = "tag_error"
        page.error = str(error)[:500]
        return "erreur tags"

    lang = str(meta.get("lang") or config.lang)
    relative = digest_relative(page.normalized_url)
    target = safe_join(config.out_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_digest(
            title=title,
            source=page.normalized_url,
            content_hash=page.content_hash or "",
            summary_hash=page.summary_hash,
            summary_model=str(meta.get("model") or ""),
            model=config.classify_model,
            taxonomy_sha256=taxonomy.sha256,
            tags=tags,
            body=summary,
            lang=lang if lang in {"fr", "de", "en"} else config.lang,
        ),
        encoding="utf-8",
        newline="\n",
    )
    cache.put_classification(
        source=page.normalized_url,
        summary_hash=page.summary_hash,
        model=config.classify_model,
        taxonomy_sha256=taxonomy.sha256,
        output_file=relative,
    )
    page.status = "tagged"
    page.tags = tags.as_dict()
    page.missing_unit = missing_unit_of(annotation)
    page.digest_path = relative
    return "tags"
