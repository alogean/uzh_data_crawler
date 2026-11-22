from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from uzh_crawler.store.paths import safe_join

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  source TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  etag TEXT,
  last_modified TEXT,
  output_file TEXT NOT NULL,
  model TEXT NOT NULL,
  taxonomy_sha256 TEXT NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


@dataclass(frozen=True)
class CacheEntry:
    source: str
    content_hash: str
    etag: str | None
    last_modified: str | None
    output_file: str
    model: str
    taxonomy_sha256: str
    updated_at: int


class DigestCache:
    """Skip the LLM when the markdown, model, and taxonomy hash are unchanged."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, source: str) -> CacheEntry | None:
        row = self._conn.execute(
            """
            SELECT source, content_hash, etag, last_modified, output_file,
                   model, taxonomy_sha256, updated_at
            FROM documents WHERE source = ?
            """,
            (source,),
        ).fetchone()
        if row is None:
            return None
        return CacheEntry(*row)

    def put(
        self,
        *,
        source: str,
        content_hash: str,
        etag: str | None,
        last_modified: str | None,
        output_file: str,
        model: str,
        taxonomy_sha256: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO documents (
              source, content_hash, etag, last_modified, output_file,
              model, taxonomy_sha256, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
              content_hash = excluded.content_hash,
              etag = excluded.etag,
              last_modified = excluded.last_modified,
              output_file = excluded.output_file,
              model = excluded.model,
              taxonomy_sha256 = excluded.taxonomy_sha256,
              updated_at = excluded.updated_at
            """,
            (
                source,
                content_hash,
                etag,
                last_modified,
                output_file,
                model,
                taxonomy_sha256,
                int(time.time()),
            ),
        )
        self._conn.commit()

    def reusable(
        self,
        *,
        source: str,
        content_hash: str,
        model: str,
        taxonomy_sha256: str,
        run_dir: Path,
    ) -> bool:
        entry = self.get(source)
        if entry is None:
            return False
        if entry.content_hash != content_hash or entry.model != model:
            return False
        if entry.taxonomy_sha256 != taxonomy_sha256:
            return False
        try:
            digest = safe_join(run_dir, entry.output_file)
        except ValueError:
            return False
        return digest.is_file()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DigestCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
