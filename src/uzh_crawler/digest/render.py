from __future__ import annotations

import json
import re

import yaml

from uzh_crawler.digest.prompt import SUMMARY_HEADINGS
from uzh_crawler.taxonomy import Tags

_ID = re.compile(r"[A-Za-z0-9_-]+")
_HEADING = re.compile(
    r"^##\s+(?:" + "|".join(SUMMARY_HEADINGS.values()) + r")\s*",
    re.IGNORECASE,
)


def render_digest(
    *,
    title: str,
    source: str,
    content_hash: str,
    model: str,
    taxonomy_sha256: str,
    tags: Tags,
    resume: str,
    lang: str,
) -> str:
    heading = SUMMARY_HEADINGS[lang]
    body = _HEADING.sub("", resume.strip()).strip()
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"source: {json.dumps(source, ensure_ascii=False)}",
        f"content_hash: {json.dumps(content_hash, ensure_ascii=False)}",
        f"model: {json.dumps(model, ensure_ascii=False)}",
        f"taxonomy_sha256: {json.dumps(taxonomy_sha256, ensure_ascii=False)}",
        "tags:",
        f"  organisation: {_scalar(tags.organisation)}",
        "  publics:",
    ]
    lines.extend(f"    - {_scalar(item)}" for item in tags.publics)
    lines.append(f"  types_document: {_scalar(tags.types_document)}")
    if tags.offre_etudes:
        lines.append("  offre_etudes:")
        lines.extend(f"    - {_scalar(item)}" for item in tags.offre_etudes)
    else:
        lines.append("  offre_etudes: []")
    lines.extend(["---", "", f"## {heading}", "", body, ""])
    return "\n".join(lines)


def parse_digest(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("digest is missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("digest frontmatter is not closed")
    meta = yaml.safe_load(text[4:end])
    if not isinstance(meta, dict):
        raise ValueError("digest frontmatter must be a mapping")
    return meta, text[end + 5 :]


def _scalar(value: str) -> str:
    if _ID.fullmatch(value):
        return value
    return json.dumps(value, ensure_ascii=False)
