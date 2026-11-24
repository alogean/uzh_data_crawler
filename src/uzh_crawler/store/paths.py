from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")


def artifact_stem(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]


def safe_join(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
        raise ValueError(f"path escapes run directory: {relative}")
    root_resolved = root.resolve()
    path = (root_resolved / relative).resolve()
    if path != root_resolved and root_resolved not in path.parents:
        raise ValueError(f"path escapes run directory: {relative}")
    return path


def file_relative(normalized_url: str, kind: str) -> str:
    """Path of the original bytes. ``kind`` is html, pdf, text or other."""
    suffix = {"html": ".html", "pdf": ".pdf", "text": ".txt"}.get(kind)
    if suffix is None:
        found = Path(urlsplit(normalized_url).path).suffix.lower()
        suffix = found if _SUFFIX.fullmatch(found) else ".bin"
    return f"files/{artifact_stem(normalized_url)}{suffix}"


def raw_relative(normalized_url: str) -> str:
    return f"raw/{artifact_stem(normalized_url)}.md"


def links_relative(normalized_url: str) -> str:
    return f"raw/{artifact_stem(normalized_url)}.links.txt"


def summary_relative(normalized_url: str) -> str:
    return f"summaries/{artifact_stem(normalized_url)}.md"


def digest_relative(normalized_url: str) -> str:
    return f"digests/{artifact_stem(normalized_url)}.md"
