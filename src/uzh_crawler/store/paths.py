from __future__ import annotations

import hashlib
from pathlib import Path


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


def raw_relative(normalized_url: str) -> str:
    return f"raw/{artifact_stem(normalized_url)}.md"


def links_relative(raw_path: str) -> str:
    return str(Path(raw_path).with_suffix(".links.txt"))


def digest_relative(normalized_url: str) -> str:
    return f"digests/{artifact_stem(normalized_url)}.md"
