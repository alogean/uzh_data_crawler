"""Quality rules that taxonomy.json must satisfy.

The rules come from the W3C SKOS Reference (integrity conditions), from the
quality issues catalogued by qSKOS, and from ANSI/NISO Z39.19 (warrant,
history, deprecation). Each rule has a code, a level and one sentence; the
same table is in docs/taxonomy.md. An ``error`` blocks a change, a
``warning`` is reported and does not.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from uzh_crawler.gate.store import LANGS, VERSION
from uzh_crawler.taxonomy import DEPRECATED, REQUIRED_AXES

ERROR = "error"
WARNING = "warning"

RULES: dict[str, tuple[str, str]] = {
    "axes-required": (ERROR, "The four axes exist, each once."),
    "version-format": (ERROR, "The taxonomy has a version MAJOR.MINOR.PATCH."),
    "uri-base": (ERROR, "uri_base is an http(s) URI ending in / or #."),
    "fields-known": (ERROR, "A node has only known fields, and its texts only the languages de, fr, en."),
    "id-format": (ERROR, "An id is lower case letters and digits joined by underscores."),
    "id-unique": (ERROR, "An id appears once in the whole file and is not the id of an axis."),
    "parent-known": (ERROR, "A parent is a node of the same axis."),
    "hierarchy-cycle": (ERROR, "Following the parents never leads back to the node."),
    "labels-complete": (ERROR, "A node has a label in de, fr and en, not blank, without control characters."),
    "definition-complete": (ERROR, "A node has a definition in de, fr and en."),
    "label-unique": (ERROR, "Two active nodes of an axis never share a label in the same language."),
    "labels-disjoint": (ERROR, "An alternative label differs from the node's own label and is not repeated."),
    "status-known": (ERROR, "A status is defined in status_legend."),
    "replacement-valid": (ERROR, "Only a deprecated node has replaced_by, and it names active nodes of the same axis."),
    "deprecated-parent": (ERROR, "An active node never hangs under a deprecated one."),
    "axis-active": (ERROR, "Every axis keeps at least one active node."),
    "alt-label-clash": (WARNING, "An alternative label is not the label of another active node of the axis."),
    "sources-present": (WARNING, "An accepted node names at least one source page."),
}

NODE_FIELDS = (
    "id", "parent", "labels", "alt_labels", "definition", "scope_note", "status", "sources", "replaced_by",
)
TEXT_FIELDS = ("labels", "definition", "scope_note")
NODE_ID = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


@dataclass(frozen=True)
class Issue:
    rule: str
    node: str | None
    message: str

    @property
    def level(self) -> str:
        return RULES[self.rule][0]

    def __str__(self) -> str:
        where = f"{self.node}: " if self.node else ""
        return f"{self.level}: {self.rule}: {where}{self.message}"


def errors_of(issues: list[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.level == ERROR]


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def check_taxonomy(data: dict) -> list[Issue]:
    """Every rule of RULES, applied to the raw content of taxonomy.json."""
    issues: list[Issue] = []

    def add(rule: str, node: str | None, message: str) -> None:
        issues.append(Issue(rule, node, message))

    version = data.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        add("version-format", None, f"version is {version!r}")
    base = data.get("uri_base")
    if not isinstance(base, str) or not base.startswith(("http://", "https://")) or base[-1:] not in "/#":
        add("uri-base", None, f"uri_base is {base!r}")
    legend = data.get("status_legend") or {}

    axes = [axis for axis in data.get("axes", []) if isinstance(axis, dict)]
    axis_count = Counter(axis.get("id") for axis in axes)
    for axis_id in REQUIRED_AXES:
        if axis_count[axis_id] != 1:
            add("axes-required", None, f"axis {axis_id} appears {axis_count[axis_id]} times")

    id_count = Counter(
        node.get("id") for axis in axes for node in axis.get("nodes", []) if isinstance(node, dict)
    )
    by_id: dict[str, tuple[str, dict]] = {}
    for axis in axes:
        for node in axis.get("nodes", []):
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                by_id.setdefault(node["id"], (axis.get("id"), node))

    for axis in axes:
        axis_id = axis.get("id")
        nodes = [node for node in axis.get("nodes", []) if isinstance(node, dict)]
        if not any(node.get("status") != DEPRECATED for node in nodes):
            add("axis-active", None, f"axis {axis_id} has no active node")
        for node in nodes:
            _check_node(node, axis_id, by_id, id_count, axis_count, legend, add)
        _check_labels_across(nodes, add)
    return issues


def _check_node(node, axis_id, by_id, id_count, axis_count, legend, add) -> None:
    node_id = node.get("id")
    name = node_id if isinstance(node_id, str) else repr(node_id)
    active = node.get("status") != DEPRECATED

    unknown = [key for key in node if key not in NODE_FIELDS]
    if unknown:
        add("fields-known", name, f"unknown fields: {', '.join(unknown)}")
    for field in (*TEXT_FIELDS, "alt_labels"):
        value = node.get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            add("fields-known", name, f"{field} must map de, fr, en to text")
            continue
        strange = [lang for lang in value if lang not in LANGS]
        if strange:
            add("fields-known", name, f"{field} has languages other than de, fr, en: {', '.join(strange)}")

    if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
        add("id-format", name, "id must be lower case letters and digits joined by underscores")
    if id_count[node_id] > 1:
        add("id-unique", name, f"id appears {id_count[node_id]} times")
    if node_id in axis_count:
        add("id-unique", name, "id is also the id of an axis")

    parent = node.get("parent")
    if parent is not None:
        owner = by_id.get(parent)
        if owner is None or owner[0] != axis_id:
            add("parent-known", name, f"parent {parent!r} is not a node of axis {axis_id}")
        elif active and owner[1].get("status") == DEPRECATED:
            add("deprecated-parent", name, f"parent {parent} is deprecated")
        # Only the nodes on the loop are reported, not the ones hanging under it.
        seen = {node_id}
        current = parent
        while current is not None and current in by_id and current not in seen:
            seen.add(current)
            current = by_id[current][1].get("parent")
        if current == node_id:
            add("hierarchy-cycle", name, "its parents lead back to it")

    labels = node.get("labels") if isinstance(node.get("labels"), dict) else {}
    for lang in LANGS:
        text = labels.get(lang)
        if not isinstance(text, str) or not text.strip():
            add("labels-complete", name, f"no label in {lang}")
        elif not text.isprintable():
            add("labels-complete", name, f"label in {lang} has control characters")
    definition = node.get("definition") if isinstance(node.get("definition"), dict) else {}
    for lang in LANGS:
        text = definition.get(lang)
        if not isinstance(text, str) or not text.strip():
            add("definition-complete", name, f"no definition in {lang}")

    alt = node.get("alt_labels") if isinstance(node.get("alt_labels"), dict) else {}
    for lang, values in alt.items():
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            add("fields-known", name, f"alt_labels in {lang} must be a list of texts")
            continue
        folded = [_fold(value) for value in values]
        own = labels.get(lang)
        if isinstance(own, str) and _fold(own) in folded:
            add("labels-disjoint", name, f"alternative label in {lang} repeats the label")
        if len(set(folded)) != len(folded):
            add("labels-disjoint", name, f"alternative labels in {lang} repeat each other")

    status = node.get("status")
    if not isinstance(status, str) or status not in legend:
        add("status-known", name, f"status {status!r} is not in status_legend")

    replaced_by = node.get("replaced_by")
    if replaced_by is not None:
        if active:
            add("replacement-valid", name, "only a deprecated node has replaced_by")
        elif not isinstance(replaced_by, list):
            add("replacement-valid", name, "replaced_by must be a list of ids")
        else:
            for target in replaced_by:
                owner = by_id.get(target)
                if target == node_id:
                    add("replacement-valid", name, "a node cannot replace itself")
                elif owner is None or owner[0] != axis_id:
                    add("replacement-valid", name, f"replacement {target!r} is not a node of axis {axis_id}")
                elif owner[1].get("status") == DEPRECATED:
                    add("replacement-valid", name, f"replacement {target} is deprecated")

    sources = node.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        add("fields-known", name, "sources must be a list of URLs")
    elif status == "accepted" and not sources:
        add("sources-present", name, "no source page")


def _check_labels_across(nodes: list[dict], add) -> None:
    active = [node for node in nodes if node.get("status") != DEPRECATED]
    for lang in LANGS:
        owners: dict[str, list[str]] = {}
        for node in active:
            label = (node.get("labels") or {}).get(lang) if isinstance(node.get("labels"), dict) else None
            if isinstance(label, str) and label.strip():
                owners.setdefault(_fold(label), []).append(str(node.get("id")))
        for label, ids in owners.items():
            if len(ids) > 1:
                for node_id in ids:
                    others = ", ".join(other for other in ids if other != node_id)
                    add("label-unique", node_id, f"label in {lang} is also the label of {others}")
        for node in active:
            alt = node.get("alt_labels") if isinstance(node.get("alt_labels"), dict) else {}
            values = alt.get(lang) if isinstance(alt.get(lang), list) else []
            for value in values:
                if not isinstance(value, str):
                    continue
                clash = [other for other in owners.get(_fold(value), []) if other != node.get("id")]
                if clash:
                    add("alt-label-clash", str(node.get("id")),
                        f"alternative label {value!r} ({lang}) is the label of {', '.join(clash)}")
