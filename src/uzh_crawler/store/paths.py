from __future__ import annotations

import functools
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")


def artifact_stem(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]


def safe_join(root: Path, relative: str) -> Path:
    """The path of a run file. A path that would leave the run directory is refused."""
    if not relative or relative.startswith(("/", "\\")) or ".." in relative.replace("\\", "/").split("/"):
        raise ValueError(f"path escapes run directory: {relative}")
    return _resolved(root) / relative


@functools.lru_cache(maxsize=8)
def _resolved(root: Path) -> Path:
    # Resolving asks the file system about every folder of the path, so it is
    # done once per run directory and not once per file.
    return root.resolve()


def file_relative(normalized_url: str, kind: str) -> str:
    """Path of the original bytes. ``kind`` is html, pdf, docx, xlsx, text or other."""
    suffix = {"html": ".html", "pdf": ".pdf", "text": ".txt", "docx": ".docx", "xlsx": ".xlsx"}.get(kind)
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
