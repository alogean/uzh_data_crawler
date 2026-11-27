"""Units that classification could not name: the raw material of add proposals.

When no ``organisation`` node names the unit that publishes a page,
classification tags a broader node and writes the unit's name, as free text,
in the page's ``missing_unit``. ``unit_hints`` groups those names.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from uzh_crawler.config import ConfigError
from uzh_crawler.gate.read import normalize_text
from uzh_crawler.store.crawl_json import load_manifest
from uzh_crawler.taxonomy import DEPRECATED


def unit_hints(out_dir: Path, taxonomy_data: dict | None = None) -> list[dict]:
    """Missing units of a run, most frequent first.

    With the taxonomy, each unit also lists the nodes whose label or
    alternative label is that very name (``matches``): a sign that the node
    exists already and needs an alternative label rather than a new node.
    """
    path = out_dir / "crawl.json"
    if not path.is_file():
        raise ConfigError(f"crawl.json not found in {out_dir}")
    try:
        manifest = load_manifest(path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    names = _names(taxonomy_data) if taxonomy_data else {}
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
            **({"matches": names.get(normalize_text(group["unit"]), [])} if taxonomy_data else {}),
        }
        for group in ordered
    ]


def _names(data: dict) -> dict[str, list[str]]:
    """Every label and alternative label of an active organisation node, in plain form."""
    names: dict[str, list[str]] = {}
    for axis in data.get("axes", []):
        if axis.get("id") != "organisation":
            continue
        for node in axis.get("nodes", []):
            if node.get("status") == DEPRECATED:
                continue
            texts = list((node.get("labels") or {}).values())
            texts += [text for values in (node.get("alt_labels") or {}).values() for text in values]
            for text in texts:
                ids = names.setdefault(normalize_text(text), [])
                if node["id"] not in ids:
                    ids.append(node["id"])
    return names
