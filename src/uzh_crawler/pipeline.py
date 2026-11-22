from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from uzh_crawler.config import ConfigError, RunConfig
from uzh_crawler.crawl.extract import MIN_USEFUL_CHARS
from uzh_crawler.crawl.fetch import make_client
from uzh_crawler.crawl.frontier import crawl_frontier
from uzh_crawler.digest.llm_ollama import OllamaAnnotator
from uzh_crawler.digest.render import render_digest
from uzh_crawler.digest.schema import tags_of
from uzh_crawler.store.cache_sqlite import DigestCache
from uzh_crawler.store.crawl_json import ERROR_STATUSES, CrawlManifest, load_manifest, now_iso, save_manifest
from uzh_crawler.store.paths import digest_relative, safe_join
from uzh_crawler.taxonomy import TagValidationError, Taxonomy, load_taxonomy
from uzh_crawler.text import content_hash, normalize_markdown

logger = logging.getLogger(__name__)

_ROBOTS_ERROR = "disallowed by robots.txt"


async def execute(
    config: RunConfig,
    *,
    mode: str,
    client: httpx.AsyncClient | None = None,
    annotator=None,
) -> int:
    if mode not in {"run", "crawl-only", "digest-only"}:
        raise ConfigError(f"unknown mode: {mode}")
    taxonomy = load_taxonomy(config.taxonomy_path)
    manifest = _open_manifest(config, taxonomy, mode)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    (config.out_dir / "raw").mkdir(exist_ok=True)
    (config.out_dir / "digests").mkdir(exist_ok=True)
    _install_config(manifest, config, taxonomy, mode)
    manifest.finished_at = None
    save_manifest(config.out_dir / "crawl.json", manifest)

    owns_client = client is None and mode != "digest-only"
    if owns_client:
        client = make_client(concurrency=config.crawl_concurrency)
    ollama_client: httpx.AsyncClient | None = None
    try:
        if mode in {"run", "crawl-only"}:
            if client is None:
                raise ConfigError("crawl requires an HTTP client")
            await crawl_frontier(config, manifest, client, config.out_dir)
        if mode in {"run", "digest-only"}:
            cache = DigestCache(config.out_dir / "cache.sqlite")
            try:
                pending = [
                    page
                    for page in manifest.pages
                    if _needs_digest(page, cache, config, taxonomy, config.out_dir)
                ]
                if pending:
                    if annotator is None:
                        ollama_client = httpx.AsyncClient(
                            timeout=httpx.Timeout(600.0, connect=10.0),
                            trust_env=False,
                        )
                        annotator = OllamaAnnotator(
                            ollama_client,
                            taxonomy,
                            model=config.model,
                            ollama_url=config.ollama_url,
                            temperature=config.temperature,
                            num_ctx=config.num_ctx,
                        )
                    prepare = getattr(annotator, "prepare", None)
                    if prepare is not None:
                        await prepare()
                    await _digest_pending(
                        pending=pending,
                        config=config,
                        taxonomy=taxonomy,
                        cache=cache,
                        annotator=annotator,
                        manifest=manifest,
                    )
            finally:
                cache.close()
        manifest.finished_at = now_iso()
        save_manifest(config.out_dir / "crawl.json", manifest)
    finally:
        if owns_client and client is not None:
            await client.aclose()
        if ollama_client is not None:
            await ollama_client.aclose()

    stats = manifest.stats()
    logger.info(
        "%s: discovered=%s fetched_ok=%s failed=%s digested=%s",
        manifest.run_id,
        stats["urls_discovered"],
        stats["urls_fetched_ok"],
        stats["urls_failed"],
        stats["urls_digested"],
    )
    if any(_is_run_error(page) for page in manifest.pages):
        return 2
    return 0


def _open_manifest(config: RunConfig, taxonomy: Taxonomy, mode: str) -> CrawlManifest:
    path = config.out_dir / "crawl.json"
    if path.is_file():
        try:
            manifest = load_manifest(path)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if config.seed_url and manifest.seed_url != config.seed_url:
            raise ConfigError(
                f"seed {config.seed_url} does not match this run ({manifest.seed_url})"
            )
        return manifest
    if mode == "digest-only":
        raise ConfigError(f"crawl.json not found in {config.out_dir}")
    if not config.seed_url:
        raise ConfigError("a seed URL is required")
    return CrawlManifest(
        run_id=config.out_dir.name or "run",
        seed_url=config.seed_url,
        started_at=now_iso(),
        config=config.manifest_config(taxonomy.sha256),
    )


def _install_config(manifest: CrawlManifest, config: RunConfig, taxonomy: Taxonomy, mode: str) -> None:
    fresh = config.manifest_config(taxonomy.sha256)
    if mode != "digest-only":
        manifest.config = fresh
        return
    # digest-only does not carry the crawl limits; keep the ones already stored.
    merged = dict(manifest.config)
    for key in (
        "model",
        "taxonomy_path",
        "taxonomy_sha256",
        "lang",
        "llm_concurrency",
        "num_ctx",
        "temperature",
    ):
        merged[key] = fresh[key]
    manifest.config = merged


def _needs_digest(page, cache: DigestCache, config: RunConfig, taxonomy: Taxonomy, run_dir: Path) -> bool:
    if page.status not in {"fetched", "digest_ok", "digest_error"}:
        return False
    if not page.raw_path or not page.content_hash:
        return page.status == "digest_error"
    if page.status == "fetched":
        return True
    if page.status == "digest_error":
        return True
    # A 304 or an identical hash still goes through the LLM when the model or the
    # taxonomy file changed. The previous digest stays until the new one is written.
    return not cache.reusable(
        source=page.normalized_url,
        content_hash=page.content_hash,
        model=config.model,
        taxonomy_sha256=taxonomy.sha256,
        run_dir=run_dir,
    )


async def _digest_pending(*, pending, config: RunConfig, taxonomy: Taxonomy, cache: DigestCache, annotator, manifest) -> None:
    semaphore = asyncio.Semaphore(max(config.llm_concurrency, 1))

    async def one(page) -> None:
        async with semaphore:
            await _digest_page(
                page=page,
                config=config,
                taxonomy=taxonomy,
                cache=cache,
                annotator=annotator,
                manifest=manifest,
            )

    await asyncio.gather(*(one(page) for page in pending))


async def _digest_page(*, page, config: RunConfig, taxonomy: Taxonomy, cache: DigestCache, annotator, manifest) -> None:
    # Drop the previous tags before the call so a crash cannot keep stale tags
    # under a new model name.
    page.status = "fetched"
    page.tags = None
    page.digest_path = None
    page.error = None
    save_manifest(config.out_dir / "crawl.json", manifest)
    try:
        raw_path = safe_join(config.out_dir, page.raw_path)
        markdown = normalize_markdown(raw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        page.status = "digest_error"
        page.error = str(exc)[:500]
        save_manifest(config.out_dir / "crawl.json", manifest)
        logger.info("digest_error %s", page.normalized_url)
        return
    page.content_hash = content_hash(markdown)
    if len(markdown) < MIN_USEFUL_CHARS:
        page.status = "empty"
        page.tags = None
        page.digest_path = None
        page.error = None
        save_manifest(config.out_dir / "crawl.json", manifest)
        logger.info("empty %s", page.normalized_url)
        return

    annotation = None
    error: Exception | None = None
    for _attempt in range(2):
        try:
            annotation = await annotator.annotate(
                url=page.normalized_url,
                markdown=markdown,
                lang=config.lang,
            )
            break
        except (TagValidationError, httpx.HTTPError, json.JSONDecodeError) as exc:
            error = exc
    if annotation is None:
        page.status = "digest_error"
        page.error = str(error)[:500]
        save_manifest(config.out_dir / "crawl.json", manifest)
        logger.info("digest_error %s", page.normalized_url)
        return

    try:
        tags = taxonomy.validate_tags(tags_of(annotation))
    except TagValidationError as exc:
        page.status = "digest_error"
        page.error = str(exc)[:500]
        save_manifest(config.out_dir / "crawl.json", manifest)
        logger.info("digest_error %s", page.normalized_url)
        return

    relative = digest_relative(page.normalized_url)
    target = safe_join(config.out_dir, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    title = str(annotation.title).strip() or page.title or page.normalized_url
    document = render_digest(
        title=title,
        source=page.normalized_url,
        content_hash=page.content_hash,
        model=config.model,
        taxonomy_sha256=taxonomy.sha256,
        tags=tags,
        resume=str(annotation.resume_markdown),
        lang=config.lang,
    )
    target.write_text(document, encoding="utf-8", newline="\n")
    cache.put(
        source=page.normalized_url,
        content_hash=page.content_hash,
        etag=page.etag,
        last_modified=page.last_modified,
        output_file=relative,
        model=config.model,
        taxonomy_sha256=taxonomy.sha256,
    )
    page.status = "digest_ok"
    page.tags = tags.as_dict()
    page.digest_path = relative
    page.title = title
    page.error = None
    save_manifest(config.out_dir / "crawl.json", manifest)
    logger.info("digest_ok %s", page.normalized_url)


def _is_run_error(page) -> bool:
    if page.status == "digest_error":
        return True
    if page.status in ERROR_STATUSES and page.error == _ROBOTS_ERROR:
        return False
    return page.status in ERROR_STATUSES
