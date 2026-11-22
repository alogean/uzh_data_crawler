from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "askUZH-TaxonomyCrawler/0.1 (+local; research)"
SUMMARY_LANGUAGES = ("fr", "de", "en")


class ConfigError(ValueError):
    """The command cannot start (paths, seed, taxonomy, or Ollama)."""


@dataclass
class RunConfig:
    seed_url: str
    taxonomy_path: Path
    out_dir: Path
    model: str
    ollama_url: str = "http://localhost:11434"
    max_pages: int = 500
    max_depth: int | None = None
    allow_subdomains: bool = False
    crawl_concurrency: int = 20
    llm_concurrency: int = 2
    lang: str = "fr"
    rate_limit: float = 3.0
    ignore_robots: bool = False
    js_fallback: bool = False
    num_ctx: int = 32768
    temperature: float = 0.1

    def manifest_config(self, taxonomy_sha256: str) -> dict:
        return {
            "allow_subdomains": self.allow_subdomains,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "model": self.model,
            "taxonomy_path": str(self.taxonomy_path),
            "taxonomy_sha256": taxonomy_sha256,
            "lang": self.lang,
            "ignore_robots": self.ignore_robots,
            "rate_limit": self.rate_limit,
            "crawl_concurrency": self.crawl_concurrency,
            "llm_concurrency": self.llm_concurrency,
            "js_fallback": self.js_fallback,
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
        }
