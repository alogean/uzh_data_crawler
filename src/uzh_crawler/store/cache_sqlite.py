from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from uzh_crawler.store.paths import safe_join

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS summaries (
      source TEXT PRIMARY KEY,
      content_hash TEXT NOT NULL,
      model TEXT NOT NULL,
      lang TEXT NOT NULL,
      output_file TEXT NOT NULL,
      summary_hash TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classifications (
      source TEXT PRIMARY KEY,
      summary_hash TEXT NOT NULL,
      model TEXT NOT NULL,
      taxonomy_sha256 TEXT NOT NULL,
      output_file TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
    """,
)


class StageCache:
    """Remembers what each model call was made from, so an identical call is skipped.

    A summary depends on the Markdown, the model and the language. A
    classification depends on the summary, the model and the taxonomy file.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path)
        for statement in _SCHEMA:
            self._conn.execute(statement)
        self._conn.commit()

    def put_summary(
        self,
        *,
        source: str,
        content_hash: str,
        model: str,
        lang: str,
        output_file: str,
        summary_hash: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO summaries
              (source, content_hash, model, lang, output_file, summary_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, content_hash, model, lang, output_file, summary_hash, int(time.time())),
        )
        self._conn.commit()

    def summary_reusable(
        self,
        *,
        source: str,
        content_hash: str,
        model: str,
        lang: str,
        run_dir: Path,
    ) -> bool:
        row = self._conn.execute(
            "SELECT content_hash, model, lang, output_file FROM summaries WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None or (row[0], row[1], row[2]) != (content_hash, model, lang):
            return False
        return _exists(run_dir, row[3])

    def put_classification(
        self,
        *,
        source: str,
        summary_hash: str,
        model: str,
        taxonomy_sha256: str,
        output_file: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO classifications
              (source, summary_hash, model, taxonomy_sha256, output_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, summary_hash, model, taxonomy_sha256, output_file, int(time.time())),
        )
        self._conn.commit()

    def classification_reusable(
        self,
        *,
        source: str,
        summary_hash: str,
        model: str,
        taxonomy_sha256: str,
        run_dir: Path,
    ) -> bool:
        row = self._conn.execute(
            """
            SELECT summary_hash, model, taxonomy_sha256, output_file
            FROM classifications WHERE source = ?
            """,
            (source,),
        ).fetchone()
        if row is None or (row[0], row[1], row[2]) != (summary_hash, model, taxonomy_sha256):
            return False
        return _exists(run_dir, row[3])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StageCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _exists(run_dir: Path, relative: str) -> bool:
    try:
        return safe_join(run_dir, relative).is_file()
    except ValueError:
        return False
