from __future__ import annotations

import hashlib
import re

_BLANK_RUN = re.compile(r"\n{3,}")


def normalize_markdown(text: str) -> str:
    """Collapse line endings and trailing space so the content hash is stable."""
    cleaned = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.split("\n")]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def content_hash(normalized_markdown: str) -> str:
    digest = hashlib.sha256(normalized_markdown.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
