from __future__ import annotations

import json
import re

from uzh_crawler.summarize.prompt import SUMMARY_HEADINGS
from uzh_crawler.summarize.summarizer import parse_frontmatter
from uzh_crawler.taxonomy import Tags

_ID = re.compile(r"[A-Za-z0-9_-]+")

parse_digest = parse_frontmatter


def render_digest(
    *,
    title: str,
    source: str,
    content_hash: str,
    summary_hash: str,
    summary_model: str,
    model: str,
    taxonomy_sha256: str,
    tags: Tags,
    body: str,
    lang: str,
) -> str:
    """The final document: the tags in the header, the summary below."""
    quoted = {
        "title": title,
        "source": source,
        "content_hash": content_hash,
        "summary_hash": summary_hash,
        "summary_model": summary_model,
        "model": model,
        "taxonomy_sha256": taxonomy_sha256,
    }
    lines = ["---"]
    lines.extend(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in quoted.items())
    lines.extend(["tags:", f"  organisation: {_scalar(tags.organisation)}", "  publics:"])
    lines.extend(f"    - {_scalar(item)}" for item in tags.publics)
    lines.append(f"  types_document: {_scalar(tags.types_document)}")
    if tags.offre_etudes:
        lines.append("  offre_etudes:")
        lines.extend(f"    - {_scalar(item)}" for item in tags.offre_etudes)
    else:
        lines.append("  offre_etudes: []")
    lines.extend(["---", "", f"## {SUMMARY_HEADINGS[lang]}", "", body.strip(), ""])
    return "\n".join(lines)


def _scalar(value: str) -> str:
    if _ID.fullmatch(value):
        return value
    return json.dumps(value, ensure_ascii=False)
