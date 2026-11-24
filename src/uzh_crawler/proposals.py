"""Candidate taxonomy nodes, kept apart from taxonomy.json until a human accepts them.

Classification records, as free text, the unit that publishes a document when
no node names it (``missing_unit``). ``hints`` groups those names. A proposal
is then written to taxonomy.proposals.json; ``check`` verifies the file and
``accept`` moves chosen nodes into taxonomy.json.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from uzh_crawler.config import ConfigError
from uzh_crawler.store.crawl_json import load_manifest
from uzh_crawler.taxonomy import REQUIRED_AXES, taxonomy_from_dict

PROPOSALS_FILE = "taxonomy.proposals.json"
LANGS = ("de", "fr", "en")
ACCEPTED_STATUS = "accepted"
_ACCEPTED_LEGEND = {
    "de": "Aus einem Crawl vorgeschlagen und von einer Person angenommen.",
    "fr": "Proposé à partir d'un crawl et accepté par une personne.",
    "en": "Proposed from a crawl and accepted by a person.",
}
_NODE_ID = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_INLINE_SOURCES = re.compile(r'"sources": \[\s*((?:"[^"\n]*",?\s*)*)\]')


def unit_hints(out_dir: Path) -> list[dict]:
    """Units that classification could not name, most frequent first."""
    path = out_dir / "crawl.json"
    if not path.is_file():
        raise ConfigError(f"crawl.json not found in {out_dir}")
    try:
        manifest = load_manifest(path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    groups: dict[str, dict] = {}
    for page in manifest.pages:
        if page.status != "tagged" or not page.missing_unit or not page.tags:
            continue
        key = " ".join(page.missing_unit.casefold().split())
        group = groups.setdefault(
            key,
            {"unit": page.missing_unit, "pages": 0, "fallbacks": Counter(), "hosts": Counter(), "examples": []},
        )
        group["pages"] += 1
        group["fallbacks"][page.tags.get("organisation")] += 1
        group["hosts"][page.normalized_url.split("/")[2]] += 1
        if len(group["examples"]) < 5:
            group["examples"].append(page.normalized_url)
    ordered = sorted(groups.values(), key=lambda group: (-group["pages"], group["unit"]))
    return [
        {
            "unit": group["unit"],
            "pages": group["pages"],
            "tagged_as": dict(group["fallbacks"].most_common()),
            "hosts": dict(group["hosts"].most_common()),
            "examples": group["examples"],
        }
        for group in ordered
    ]


def load_proposals(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"proposals file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"proposals file is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("kind") != "taxonomy_proposals":
        raise ConfigError("proposals kind must be taxonomy_proposals")
    if not isinstance(data.get("proposals"), list):
        raise ConfigError("proposals must be a list")
    return data


def check_proposals(taxonomy_data: dict, proposals: dict) -> list[str]:
    """Problems that would stop a proposal from entering the taxonomy."""
    errors: list[str] = []
    known: dict[str, str] = {}
    for axis in taxonomy_data.get("axes", []):
        for node in axis.get("nodes", []):
            known[node["id"]] = axis["id"]
    seen: set[str] = set()
    for index, proposal in enumerate(proposals["proposals"]):
        where = f"proposal {index + 1}"
        if not isinstance(proposal, dict) or not isinstance(proposal.get("node"), dict):
            errors.append(f"{where}: needs an axis and a node object")
            continue
        node = proposal["node"]
        node_id = node.get("id")
        where = f"{where} ({node_id})"
        axis_id = proposal.get("axis")
        if axis_id not in REQUIRED_AXES:
            errors.append(f"{where}: unknown axis {axis_id!r}")
        if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
            errors.append(f"{where}: id must be lower case letters, digits and underscores")
        elif node_id in known or node_id in seen:
            errors.append(f"{where}: id already exists")
        else:
            seen.add(node_id)
        parent = node.get("parent")
        if parent is not None and known.get(parent) != axis_id:
            errors.append(f"{where}: parent {parent!r} is not a node of axis {axis_id}")
        for field in ("labels", "definition"):
            value = node.get(field)
            if not isinstance(value, dict) or any(
                not isinstance(value.get(lang), str) or not value[lang].strip() for lang in LANGS
            ):
                errors.append(f"{where}: {field} needs de, fr and en")
        sources = node.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(s, str) for s in sources):
            errors.append(f"{where}: sources needs at least one URL")
        if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
            errors.append(f"{where}: reason is required")
        if isinstance(node_id, str) and axis_id in REQUIRED_AXES:
            # A later proposal may hang under this one.
            known.setdefault(node_id, axis_id)
    return errors


def accept_proposals(taxonomy_path: Path, proposals_path: Path, ids: list[str]) -> list[str]:
    """Move the named proposals into taxonomy.json. Returns the ids that were moved."""
    taxonomy_data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    proposals = load_proposals(proposals_path)
    by_id = {
        proposal.get("node", {}).get("id"): proposal
        for proposal in proposals["proposals"]
        if isinstance(proposal, dict) and isinstance(proposal.get("node"), dict)
    }
    missing = [node_id for node_id in ids if node_id not in by_id]
    if missing:
        raise ConfigError(f"no proposal with id: {', '.join(missing)}")
    wanted = {"schema_version": 1, "kind": "taxonomy_proposals",
              "proposals": [by_id[node_id] for node_id in ids]}
    errors = check_proposals(taxonomy_data, wanted)
    if errors:
        raise ConfigError("\n".join(errors))

    axes = {axis["id"]: axis for axis in taxonomy_data["axes"]}
    for node_id in ids:
        proposal = by_id[node_id]
        source = proposal["node"]
        node = {
            "id": source["id"],
            "parent": source.get("parent"),
            "labels": {lang: source["labels"][lang] for lang in LANGS},
            "definition": {lang: source["definition"][lang] for lang in LANGS},
            "status": ACCEPTED_STATUS,
            "sources": list(source["sources"]),
        }
        axes[proposal["axis"]]["nodes"].append(node)
    taxonomy_data.setdefault("status_legend", {}).setdefault(ACCEPTED_STATUS, _ACCEPTED_LEGEND)
    taxonomy_from_dict(taxonomy_data)  # the result must still load
    proposals["proposals"] = [
        proposal for proposal in proposals["proposals"]
        if not (isinstance(proposal, dict) and proposal.get("node", {}).get("id") in ids)
    ]
    _write(taxonomy_path, dump_taxonomy(taxonomy_data))
    _write(proposals_path, json.dumps(proposals, ensure_ascii=False, indent=2) + "\n")
    return list(ids)


def dump_taxonomy(data: dict) -> str:
    """taxonomy.json as the repository formats it: two spaces, sources on one line."""
    text = json.dumps(data, ensure_ascii=False, indent=2)

    def inline(match: re.Match) -> str:
        items = re.findall(r'"[^"\n]*"', match.group(1))
        return '"sources": [' + ", ".join(items) + "]"

    return _INLINE_SOURCES.sub(inline, text) + "\n"


def _write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
