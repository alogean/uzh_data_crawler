from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "askUZH-TaxonomyCrawler/0.1 (+local; research)"
SUMMARY_LANGUAGES = ("fr", "de", "en")
# Each model stage has its own default: summarizing was measured to be much faster
# with a small model and more parallel calls (docs/summarize-performance.md).
SUMMARY_MODEL = "gemma4:e2b"
CLASSIFY_MODEL = "qwen3.5:9b"
SUMMARY_CONCURRENCY = 4
CLASSIFY_CONCURRENCY = 2

# The keys of the manifest config that each command owns. A command rewrites
# only its own keys, so a later `summarize` cannot change the recorded scope.
SCOPE_KEYS = (
    "seeds",
    "allow_subdomains",
    "include_prefixes",
    "exclude_path_contains",
    "discover_sitemaps",
)
CONFIG_KEYS = {
    "crawl": SCOPE_KEYS
    + ("max_pages", "max_depth", "sitemap_only", "rate_limit", "burst", "crawl_concurrency", "ignore_robots"),
    "scrape": ("rate_limit", "burst", "crawl_concurrency", "ignore_robots", "js_fallback"),
    "summarize": ("summary_model", "lang", "num_ctx", "temperature", "llm_concurrency"),
    "classify": (
        "classify_model",
        "taxonomy_path",
        "taxonomy_sha256",
        "lang",
        "num_ctx",
        "temperature",
        "llm_concurrency",
    ),
}
CONFIG_KEYS["run"] = tuple(
    dict.fromkeys(key for mode in ("crawl", "scrape", "summarize", "classify") for key in CONFIG_KEYS[mode])
)


class ConfigError(ValueError):
    """The command cannot start (paths, seed, taxonomy, or Ollama)."""


@dataclass
class RunConfig:
    out_dir: Path
    seed_url: str = ""
    seed_urls: tuple[str, ...] = ()
    taxonomy_path: Path | None = None
    summary_model: str = SUMMARY_MODEL
    classify_model: str = CLASSIFY_MODEL
    ollama_url: str = "http://localhost:11434"
    max_pages: int | None = 500
    max_depth: int | None = None
    allow_subdomains: bool = False
    crawl_concurrency: int = 20
    extract_workers: int | None = None
    llm_concurrency: int | None = None  # None: the default of each stage
    lang: str = "fr"
    rate_limit: float = 3.0
    burst: int = 20
    ignore_robots: bool = False
    js_fallback: bool = False
    num_ctx: int = 32768
    temperature: float = 0.1
    include_prefixes: dict[str, tuple[str, ...]] | None = None
    exclude_path_contains: tuple[str, ...] = ()
    discover_sitemaps: bool = True
    sitemap_only: bool = False
    refresh: bool = False

    def __post_init__(self) -> None:
        if self.include_prefixes is None:
            self.include_prefixes = {}
        if not self.seed_urls and self.seed_url:
            self.seed_urls = (self.seed_url,)
        elif self.seed_urls and not self.seed_url:
            self.seed_url = self.seed_urls[0]

    def manifest_config(self, taxonomy_sha256: str | None = None) -> dict:
        return {
            "seeds": list(self.seed_urls),
            "allow_subdomains": self.allow_subdomains,
            "include_prefixes": {
                host: list(prefixes)
                for host, prefixes in sorted((self.include_prefixes or {}).items())
            },
            "exclude_path_contains": list(self.exclude_path_contains),
            "discover_sitemaps": self.discover_sitemaps,
            "sitemap_only": self.sitemap_only,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "rate_limit": self.rate_limit,
            "burst": self.burst,
            "crawl_concurrency": self.crawl_concurrency,
            "ignore_robots": self.ignore_robots,
            "js_fallback": self.js_fallback,
            "summary_model": self.summary_model,
            "classify_model": self.classify_model,
            "lang": self.lang,
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "llm_concurrency": self.llm_concurrency,
            "taxonomy_path": None if self.taxonomy_path is None else str(self.taxonomy_path),
            "taxonomy_sha256": taxonomy_sha256,
        }

    def adopt_scope(self, stored: dict) -> None:
        """Take the scope a previous `crawl` recorded for this run directory."""
        seeds = stored.get("seeds")
        if isinstance(seeds, list) and seeds:
            self.seed_urls = tuple(str(seed) for seed in seeds)
            self.seed_url = self.seed_urls[0]
        self.allow_subdomains = bool(stored.get("allow_subdomains", False))
        prefixes = stored.get("include_prefixes") or {}
        self.include_prefixes = {host: tuple(values) for host, values in prefixes.items()}
        self.exclude_path_contains = tuple(stored.get("exclude_path_contains") or ())
        self.discover_sitemaps = bool(stored.get("discover_sitemaps", False))
