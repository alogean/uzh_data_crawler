"""Runs the stages. Each one reads and updates the same run directory.

    crawl      list the URLs of a site          -> crawl.json, urls.txt
    scrape     download and extract             -> files/, raw/
    summarize  local model, one document        -> summaries/
    classify   local model, tags the summary    -> digests/

``run`` chains the four. Every stage can also be called alone, on the whole
run or on the URLs given.

Each stage is imported when it runs. A crawl then starts without loading the
Markdown, PDF and model libraries it does not use.
"""

from __future__ import annotations

import logging

import httpx

from uzh_crawler.config import CONFIG_KEYS, SCOPE_KEYS, ConfigError, RunConfig
from uzh_crawler.progress import Progress
from uzh_crawler.scope import Scope
from uzh_crawler.scrape.fetch import make_client
from uzh_crawler.store.crawl_json import (
    ERROR_STATUSES,
    ROBOTS_ERROR,
    STORED_STATUSES,
    CrawlManifest,
    Page,
    load_manifest,
    now_iso,
    save_manifest,
)
from uzh_crawler.taxonomy import Taxonomy, load_taxonomy
from uzh_crawler.urls import host_of, is_uzh_host, normalize_url

logger = logging.getLogger(__name__)

STAGES = {
    "crawl": ("crawl",),
    "scrape": ("scrape",),
    "summarize": ("summarize",),
    "classify": ("classify",),
    "run": ("crawl", "scrape", "summarize", "classify"),
}


async def execute(
    config: RunConfig,
    *,
    mode: str,
    urls: tuple[str, ...] = (),
    client: httpx.AsyncClient | None = None,
    summarizer=None,
    classifier=None,
) -> int:
    if mode not in STAGES:
        raise ConfigError(f"unknown mode: {mode}")
    stages = STAGES[mode]
    targets = _normalize_targets(urls)
    taxonomy: Taxonomy | None = None
    if "classify" in stages:
        if config.taxonomy_path is None:
            raise ConfigError("a taxonomy file is required")
        taxonomy = load_taxonomy(config.taxonomy_path)
    manifest = _open_manifest(config, mode, targets)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    _install_config(manifest, config, taxonomy, mode)
    manifest.finished_at = None
    manifest_path = config.out_dir / "crawl.json"
    save_manifest(manifest_path, manifest)
    scope = Scope.from_config(config)

    needs_http = "crawl" in stages or "scrape" in stages
    owns_client = client is None and needs_http
    if owns_client:
        client = make_client(concurrency=config.crawl_concurrency)
    ollama_client: httpx.AsyncClient | None = None
    cache = None

    def chat():
        from uzh_crawler.llm.ollama import OllamaChat

        nonlocal ollama_client
        if ollama_client is None:
            ollama_client = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=10.0),
                trust_env=False,
            )
        return OllamaChat(
            ollama_client,
            ollama_url=config.ollama_url,
            temperature=config.temperature,
            num_ctx=config.num_ctx,
        )

    try:
        if "crawl" in stages:
            from uzh_crawler.crawl.discover import discover, write_url_list

            progress = Progress("crawl", config.max_pages)
            depth = "libre" if config.max_depth is None else str(config.max_depth)
            ceiling = "sans plafond" if config.max_pages is None else f"plafond {config.max_pages} pages"
            progress.note(f"{len(config.seed_urls)} URL de départ · profondeur {depth} · {ceiling}")
            await discover(config, scope, manifest, client, config.out_dir, progress)
            listed = write_url_list(config.out_dir, manifest)
            progress.note(f"{listed} URL dans urls.txt")

        if "scrape" in stages:
            from uzh_crawler.scrape.scraper import scrape_pages

            pages = _scrape_targets(manifest, config, targets, mode)
            progress = Progress("scrape", len(pages) or None)
            progress.note(f"{len(pages)} URL à télécharger ou à relire")
            await scrape_pages(
                config, scope, manifest, client, config.out_dir, pages, progress,
                explicit=bool(targets) and mode == "scrape",
            )

        if "summarize" in stages or "classify" in stages:
            from uzh_crawler.store.cache_sqlite import StageCache

            cache = StageCache(config.out_dir / "cache.sqlite")

        if "summarize" in stages:
            from uzh_crawler.summarize.summarizer import (
                OllamaSummarizer,
                needs_summary,
                summarize_pages,
            )

            candidates = _known_pages(manifest, targets) if mode == "summarize" and targets else manifest.pages
            forced = mode == "summarize" and bool(targets)
            pending = [
                page for page in candidates
                if (forced and page.raw_path and page.status in STORED_STATUSES and page.status != "empty")
                or needs_summary(page, cache, config, config.out_dir)
            ]
            progress = Progress("summarize", len(pending) or None)
            _announce(progress, pending, len(candidates), f"à résumer avec {config.summary_model}")
            if pending:
                if summarizer is None:
                    summarizer = OllamaSummarizer(chat(), model=config.summary_model)
                await _prepare(summarizer)
                await summarize_pages(config, manifest, cache, summarizer, pending, progress)

        if "classify" in stages:
            from uzh_crawler.classify.classifier import (
                OllamaClassifier,
                classify_pages,
                needs_classification,
            )

            candidates = _known_pages(manifest, targets) if mode == "classify" and targets else manifest.pages
            forced = mode == "classify" and bool(targets)
            pending = [
                page for page in candidates
                if (forced and page.summary_path)
                or needs_classification(page, cache, config, taxonomy, config.out_dir)
            ]
            progress = Progress("classify", len(pending) or None)
            _announce(progress, pending, len(candidates), f"à classer avec {config.classify_model}")
            if pending:
                if classifier is None:
                    classifier = OllamaClassifier(chat(), taxonomy, model=config.classify_model)
                await _prepare(classifier)
                await classify_pages(config, taxonomy, manifest, cache, classifier, pending, progress)

        manifest.finished_at = now_iso()
        save_manifest(manifest_path, manifest)
    finally:
        if cache is not None:
            cache.close()
        if owns_client and client is not None:
            await client.aclose()
        if ollama_client is not None:
            await ollama_client.aclose()

    stats = manifest.stats()
    logger.info(
        "%s: discovered=%s fetched_ok=%s failed=%s summarized=%s tagged=%s",
        manifest.run_id,
        stats["urls_discovered"],
        stats["urls_fetched_ok"],
        stats["urls_failed"],
        stats["urls_summarized"],
        stats["urls_tagged"],
    )
    if any(_is_run_error(page) for page in manifest.pages):
        return 2
    return 0


def _announce(progress: Progress, pending: list[Page], candidates: int, what: str) -> None:
    if pending:
        progress.note(f"{len(pending)} document(s) {what}")
    else:
        progress.note(f"rien à recalculer sur {candidates} URL · modèle non rappelé")


async def _prepare(worker) -> None:
    prepare = getattr(worker, "prepare", None)
    if prepare is not None:
        await prepare()


def _normalize_targets(urls: tuple[str, ...]) -> tuple[str, ...]:
    targets: list[str] = []
    for raw in urls:
        try:
            url = normalize_url(raw)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if not is_uzh_host(host_of(url)):
            raise ConfigError(f"URL is outside uzh.ch: {url}")
        if url not in targets:
            targets.append(url)
    return tuple(targets)


def _open_manifest(config: RunConfig, mode: str, targets: tuple[str, ...]) -> CrawlManifest:
    path = config.out_dir / "crawl.json"
    crawling = "crawl" in STAGES[mode]
    if path.is_file():
        try:
            manifest = load_manifest(path)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if crawling:
            stored = manifest.config.get("seeds")
            current = list(config.seed_urls)
            if isinstance(stored, list):
                if sorted(stored) != sorted(current):
                    raise ConfigError("les URL de départ ne correspondent pas à ce répertoire de run")
            elif manifest.seed_url != config.seed_url:
                raise ConfigError(
                    f"seed {config.seed_url} does not match this run ({manifest.seed_url})"
                )
            if _scope_signature(manifest.config) != _scope_signature(config.manifest_config()):
                raise ConfigError(
                    "le périmètre (préfixes, exclusions, sitemap) ne correspond pas à ce répertoire de run"
                )
        else:
            config.adopt_scope(manifest.config)
        return manifest
    if mode == "scrape" and targets:
        # Scraping a URL does not need a crawl first: the URL is its own run.
        config.seed_urls = targets
        config.seed_url = targets[0]
        config.discover_sitemaps = False
    elif not crawling:
        raise ConfigError(f"crawl.json not found in {config.out_dir}")
    if not config.seed_url:
        raise ConfigError("a seed URL is required")
    return CrawlManifest(
        run_id=config.out_dir.name or "run",
        seed_url=config.seed_url,
        started_at=now_iso(),
        config={key: config.manifest_config()[key] for key in SCOPE_KEYS},
    )


def _scope_signature(config: dict) -> dict:
    prefixes = config.get("include_prefixes") or {}
    return {
        "include_prefixes": {
            host: sorted(values) for host, values in sorted(prefixes.items())
        },
        "exclude_path_contains": sorted(config.get("exclude_path_contains") or []),
        "discover_sitemaps": bool(config.get("discover_sitemaps", False)),
    }


def _install_config(
    manifest: CrawlManifest, config: RunConfig, taxonomy: Taxonomy | None, mode: str
) -> None:
    """Record this command's settings. The other commands' settings stay as they were."""
    fresh = config.manifest_config(None if taxonomy is None else taxonomy.sha256)
    merged = dict(manifest.config)
    for key in CONFIG_KEYS[mode]:
        merged[key] = fresh[key]
    manifest.config = merged


def _scrape_targets(
    manifest: CrawlManifest, config: RunConfig, targets: tuple[str, ...], mode: str
) -> list[Page]:
    if targets and mode == "scrape":
        pages: list[Page] = []
        for url in targets:
            page = manifest.page(url)
            if page is None:
                page = Page(url=url, normalized_url=url, discovered_from=None, depth=0, status="discovered")
                manifest.upsert(page)
            pages.append(page)
        return pages
    wanted = {"discovered", "failed"}
    if config.refresh:
        wanted |= STORED_STATUSES
    return [page for page in manifest.pages if page.status in wanted]


def _known_pages(manifest: CrawlManifest, targets: tuple[str, ...]) -> list[Page]:
    pages: list[Page] = []
    for url in targets:
        page = manifest.page(url)
        if page is None:
            raise ConfigError(f"URL is not in this run: {url}")
        pages.append(page)
    return pages


def _is_run_error(page: Page) -> bool:
    if page.status == "failed" and page.error == ROBOTS_ERROR:
        return False
    return page.status in ERROR_STATUSES
