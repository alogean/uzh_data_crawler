from __future__ import annotations

from pathlib import Path

from uzh_crawler.classify.render import parse_digest
from uzh_crawler.config import ConfigError
from uzh_crawler.store.crawl_json import SUMMARIZED_STATUSES, load_manifest
from uzh_crawler.store.paths import safe_join
from uzh_crawler.summarize.summarizer import summary_hash
from uzh_crawler.taxonomy import TagValidationError, Taxonomy
from uzh_crawler.text import content_hash, normalize_markdown


def validate_run(out_dir: Path, taxonomy: Taxonomy) -> list[str]:
    """Return human-readable problems. An empty list means the run matches the taxonomy."""
    manifest_path = out_dir / "crawl.json"
    if not manifest_path.is_file():
        raise ConfigError(f"crawl.json not found in {out_dir}")
    try:
        manifest = load_manifest(manifest_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    errors: list[str] = []
    expected_sha = manifest.config.get("taxonomy_sha256")
    expected_model = manifest.config.get("classify_model")
    for page in manifest.pages:
        if page.status not in SUMMARIZED_STATUSES:
            continue
        label = page.normalized_url
        errors.extend(f"{label}: {problem}" for problem in _check_sources(out_dir, page))
        if page.status != "tagged":
            continue
        try:
            page_tags = taxonomy.validate_tags(page.tags)
        except TagValidationError as exc:
            errors.append(f"{label}: {exc}")
            continue
        try:
            meta, _body = parse_digest(_read(out_dir, page.digest_path, "digest"))
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        try:
            file_tags = taxonomy.validate_tags(meta.get("tags"))
        except TagValidationError as exc:
            errors.append(f"{label}: digest tags: {exc}")
            continue
        if file_tags != page_tags:
            errors.append(f"{label}: digest tags differ from crawl.json")
        if meta.get("source") != page.normalized_url:
            errors.append(f"{label}: digest source does not match the page URL")
        if meta.get("content_hash") != page.content_hash:
            errors.append(f"{label}: digest content_hash does not match crawl.json")
        if meta.get("summary_hash") != page.summary_hash:
            errors.append(f"{label}: digest was not made from the current summary")
        if expected_sha and meta.get("taxonomy_sha256") != expected_sha:
            errors.append(f"{label}: digest taxonomy hash does not match this run")
        if expected_model and meta.get("model") != expected_model:
            errors.append(f"{label}: digest model does not match this run")
    return errors


def _check_sources(out_dir: Path, page) -> list[str]:
    """The Markdown and the summary a page claims must still be the files on disk."""
    problems: list[str] = []
    try:
        raw = _read(out_dir, page.raw_path, "raw")
        if content_hash(normalize_markdown(raw)) != page.content_hash:
            problems.append("raw markdown hash does not match content_hash")
    except ValueError as exc:
        problems.append(str(exc))
    try:
        summary = _read(out_dir, page.summary_path, "summary")
        if summary_hash(summary) != page.summary_hash:
            problems.append("summary file hash does not match summary_hash")
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def _read(out_dir: Path, relative: str | None, what: str) -> str:
    if not relative:
        raise ValueError(f"no {what} path recorded")
    path = safe_join(out_dir, relative)
    if not path.is_file():
        raise ValueError(f"missing {what} file {relative}")
    return path.read_text(encoding="utf-8")
