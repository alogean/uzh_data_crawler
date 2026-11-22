from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_AXES = ("organisation", "publics", "types_document", "offre_etudes")


class TaxonomyError(ValueError):
    """taxonomy.json cannot be loaded or does not match the v1 contract."""


class TagValidationError(TaxonomyError):
    """A tag id is missing, duplicated in the wrong shape, or absent from the taxonomy."""


@dataclass(frozen=True)
class TaxonomyNode:
    id: str
    parent: str | None
    labels: dict[str, str]
    definition: dict[str, str]
    status: str
    sources: tuple[str, ...]
    axis_id: str

    def label(self, lang: str) -> str:
        return self.labels.get(lang) or self.labels.get("fr") or self.labels.get("en") or self.id


@dataclass(frozen=True)
class Tags:
    organisation: str
    publics: tuple[str, ...]
    types_document: str
    offre_etudes: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "organisation": self.organisation,
            "publics": list(self.publics),
            "types_document": self.types_document,
            "offre_etudes": list(self.offre_etudes),
        }


@dataclass
class Taxonomy:
    schema_version: int
    axes: dict[str, tuple[TaxonomyNode, ...]]
    sha256: str
    source_path: str | None = None

    def ids(self, axis_id: str) -> tuple[str, ...]:
        self._require_axis(axis_id)
        return tuple(node.id for node in self.axes[axis_id])

    def id_set(self, axis_id: str) -> set[str]:
        return set(self.ids(axis_id))

    def node(self, node_id: str) -> TaxonomyNode:
        for nodes in self.axes.values():
            for node in nodes:
                if node.id == node_id:
                    return node
        raise TaxonomyError(f"unknown node id: {node_id}")

    def labels_for(self, axis_id: str, lang: str) -> list[tuple[str, str]]:
        self._require_axis(axis_id)
        return [(node.id, node.label(lang)) for node in self.axes[axis_id]]

    def _require_axis(self, axis_id: str) -> None:
        if axis_id not in self.axes:
            raise TaxonomyError(f"unknown axis: {axis_id}")

    def validate_tags(self, raw: dict | None) -> Tags:
        if not isinstance(raw, dict):
            raise TagValidationError("tags must be an object")
        organisation = _one_id(raw.get("organisation"), self.id_set("organisation"), "organisation")
        publics = _many_ids(raw.get("publics"), self.id_set("publics"), "publics", minimum=1)
        types_document = _one_id(
            raw.get("types_document"), self.id_set("types_document"), "types_document"
        )
        offre_etudes = _many_ids(
            raw.get("offre_etudes", []),
            self.id_set("offre_etudes"),
            "offre_etudes",
            minimum=0,
        )
        return Tags(organisation, tuple(publics), types_document, tuple(offre_etudes))


def _one_id(value: object, allowed: set[str], field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TagValidationError(f"{field} must be a single id")
    if value not in allowed:
        raise TagValidationError(f"{field} id not in taxonomy: {value}")
    return value


def _many_ids(value: object, allowed: set[str], field: str, minimum: int) -> list[str]:
    if not isinstance(value, list):
        raise TagValidationError(f"{field} must be a list of ids")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise TagValidationError(f"{field} id not in taxonomy: {item!r}")
        if item not in seen:
            seen.append(item)
    if len(seen) < minimum:
        raise TagValidationError(f"{field} requires at least {minimum} id")
    return seen


def load_taxonomy(path: Path) -> Taxonomy:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise TaxonomyError(f"taxonomy is not valid JSON: {path}") from exc
    if not isinstance(parsed, dict):
        raise TaxonomyError("taxonomy root must be an object")
    return taxonomy_from_dict(parsed, sha256=sha, source_path=str(path))


def taxonomy_from_dict(
    data: dict,
    *,
    sha256: str = "",
    source_path: str | None = None,
) -> Taxonomy:
    if data.get("kind") != "taxonomy":
        raise TaxonomyError("kind must be taxonomy")
    if data.get("schema_version") != 1:
        raise TaxonomyError("unsupported schema_version")
    axes_raw = data.get("axes")
    if not isinstance(axes_raw, list):
        raise TaxonomyError("axes must be a list")

    axes: dict[str, tuple[TaxonomyNode, ...]] = {}
    seen: set[str] = set()
    for axis in axes_raw:
        if not isinstance(axis, dict):
            raise TaxonomyError("axis must be an object")
        axis_id = axis.get("id")
        if not isinstance(axis_id, str) or not axis_id:
            raise TaxonomyError("axis id missing")
        if axis_id in axes:
            raise TaxonomyError(f"duplicate axis: {axis_id}")
        nodes: list[TaxonomyNode] = []
        raw_nodes = axis.get("nodes") or []
        if not isinstance(raw_nodes, list):
            raise TaxonomyError(f"nodes of {axis_id} must be a list")
        for node in raw_nodes:
            if not isinstance(node, dict):
                raise TaxonomyError(f"node in {axis_id} must be an object")
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                raise TaxonomyError(f"node id missing in {axis_id}")
            if node_id in seen:
                raise TaxonomyError(f"duplicate node id: {node_id}")
            seen.add(node_id)
            parent = node.get("parent")
            if parent is not None and not isinstance(parent, str):
                raise TaxonomyError(f"parent of {node_id} must be a string or null")
            labels = node.get("labels") or {}
            definition = node.get("definition") or {}
            sources = node.get("sources") or []
            if not isinstance(labels, dict) or not isinstance(definition, dict):
                raise TaxonomyError(f"labels and definition of {node_id} must be objects")
            if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
                raise TaxonomyError(f"sources of {node_id} must be a list of strings")
            nodes.append(
                TaxonomyNode(
                    id=node_id,
                    parent=parent,
                    labels={str(key): str(value) for key, value in labels.items()},
                    definition={str(key): str(value) for key, value in definition.items()},
                    status=str(node.get("status") or ""),
                    sources=tuple(sources),
                    axis_id=axis_id,
                )
            )
        axes[axis_id] = tuple(nodes)

    missing = [axis_id for axis_id in REQUIRED_AXES if axis_id not in axes]
    if missing:
        raise TaxonomyError(f"missing axes: {', '.join(missing)}")
    for axis_id, nodes in axes.items():
        known = {node.id for node in nodes}
        for node in nodes:
            if node.parent is not None and node.parent not in known:
                raise TaxonomyError(
                    f"parent {node.parent} of {node.id} is not in axis {axis_id}"
                )
    return Taxonomy(
        schema_version=1,
        axes=axes,
        sha256=sha256,
        source_path=source_path,
    )
