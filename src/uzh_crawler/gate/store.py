"""The files of the taxonomy and how they are read and written.

Three files sit side by side, named after the taxonomy file:

- ``taxonomy.json``: the taxonomy itself.
- ``taxonomy.proposals.json``: changes waiting for a person's decision.
- ``taxonomy.history.jsonl``: one line per accepted or rejected change, never rewritten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from uzh_crawler.config import ConfigError

LANGS = ("de", "fr", "en")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
# Lists of short strings: sources, replacements, and the alternative labels of one language.
_INLINE_SOURCES = re.compile(r'"(sources|replaced_by|de|fr|en)": \[\s*((?:"[^"\n]*",?\s*)*)\]')


def proposals_path(taxonomy_path: Path) -> Path:
    return taxonomy_path.with_name(f"{taxonomy_path.stem}.proposals.json")


def history_path(taxonomy_path: Path) -> Path:
    return taxonomy_path.with_name(f"{taxonomy_path.stem}.history.jsonl")


def read_taxonomy(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"taxonomy not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"taxonomy is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("kind") != "taxonomy":
        raise ConfigError("taxonomy kind must be taxonomy")
    if not isinstance(data.get("axes"), list):
        raise ConfigError("taxonomy axes must be a list")
    return data


def dump_taxonomy(data: dict) -> str:
    """taxonomy.json as the repository formats it: two spaces, short id lists on one line."""
    text = json.dumps(data, ensure_ascii=False, indent=2)

    def inline(match: re.Match) -> str:
        items = re.findall(r'"[^"\n]*"', match.group(2))
        return f'"{match.group(1)}": [' + ", ".join(items) + "]"

    return _INLINE_SOURCES.sub(inline, text) + "\n"


def read_proposals(path: Path) -> dict:
    if not path.is_file():
        return {"schema_version": 1, "kind": "taxonomy_proposals", "proposals": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"proposals file is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("kind") != "taxonomy_proposals":
        raise ConfigError("proposals kind must be taxonomy_proposals")
    if not isinstance(data.get("proposals"), list):
        raise ConfigError("proposals must be a list")
    return data


def dump_proposals(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def read_history(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}:{number}: not a JSON line") from exc
    return entries


def append_history(path: Path, entries: list[dict]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_text(path: Path, text: str) -> None:
    """Replace a file in one step, so that a crash never leaves half of it."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def bump(version: str, level: str) -> str:
    """Next version: major, minor or patch, as in semantic versioning."""
    match = VERSION.fullmatch(version or "")
    if not match:
        raise ConfigError(f"taxonomy version must be MAJOR.MINOR.PATCH, not {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
