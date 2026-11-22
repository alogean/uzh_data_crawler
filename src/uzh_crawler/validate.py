from __future__ import annotations

from pathlib import Path

from uzh_crawler.config import ConfigError
from uzh_crawler.digest.render import parse_digest
from uzh_crawler.store.crawl_json import load_manifest
from uzh_crawler.store.paths import safe_join
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
    expected_model = manifest.config.get("model")
    for page in manifest.pages:
        if page.status != "digest_ok":
            continue
        label = page.normalized_url
        try:
            page_tags = taxonomy.validate_tags(page.tags)
        except TagValidationError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not page.digest_path:
            errors.append(f"{label}: digest_ok without digest_path")
            continue
        try:
            digest_path = safe_join(out_dir, page.digest_path)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not digest_path.is_file():
            errors.append(f"{label}: missing digest file {page.digest_path}")
            continue
        try:
            meta, _body = parse_digest(digest_path.read_text(encoding="utf-8"))
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
        if expected_sha and meta.get("taxonomy_sha256") != expected_sha:
            errors.append(f"{label}: digest taxonomy hash does not match this run")
        if expected_model and meta.get("model") != expected_model:
            errors.append(f"{label}: digest model does not match this run")
        if page.raw_path and page.content_hash:
            try:
                raw_path = safe_join(out_dir, page.raw_path)
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
                continue
            if not raw_path.is_file():
                errors.append(f"{label}: missing raw file {page.raw_path}")
                continue
            raw_hash = content_hash(normalize_markdown(raw_path.read_text(encoding="utf-8")))
            if raw_hash != page.content_hash:
                errors.append(f"{label}: raw markdown hash does not match content_hash")
    return errors
