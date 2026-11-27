"""Reading the taxonomy: a tree, one node in full, a search over ids and labels."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from uzh_crawler.config import ConfigError
from uzh_crawler.gate.changes import change_of, target
from uzh_crawler.taxonomy import DEPRECATED


@dataclass
class Index:
    """The nodes of a taxonomy with their axis, children and place in the tree."""

    data: dict

    def __post_init__(self) -> None:
        self.axes = [axis for axis in self.data.get("axes", []) if isinstance(axis, dict)]
        self.nodes: dict[str, dict] = {}
        self.axis_of: dict[str, str] = {}
        self.children: dict[str | None, list[str]] = {}
        self.roots: dict[str, list[str]] = {}
        for axis in self.axes:
            self.roots[axis["id"]] = []
            for node in axis.get("nodes", []):
                self.nodes[node["id"]] = node
                self.axis_of[node["id"]] = axis["id"]
        for axis in self.axes:
            for node in axis.get("nodes", []):
                parent = node.get("parent")
                if parent is None:
                    self.roots[axis["id"]].append(node["id"])
                else:
                    self.children.setdefault(parent, []).append(node["id"])

    def node(self, node_id: str) -> dict:
        if node_id not in self.nodes:
            raise ConfigError(f"no node with id {node_id}; try: taxonomy find {node_id}")
        return self.nodes[node_id]

    def path(self, node_id: str) -> list[str]:
        """The ids from the top of the axis down to the node, the node included."""
        path = [node_id]
        parent = self.nodes[node_id].get("parent")
        while parent is not None and parent in self.nodes and parent not in path:
            path.insert(0, parent)
            parent = self.nodes[parent].get("parent")
        return path

    def uri(self, node_id: str) -> str:
        return f"{self.data.get('uri_base', '')}{node_id}"

    def walk(self, axis_id: str):
        """(depth, id) in tree order: each node, then its children."""
        def visit(node_id: str, depth: int):
            yield depth, node_id
            for child in self.children.get(node_id, []):
                yield from visit(child, depth + 1)

        for root in self.roots[axis_id]:
            yield from visit(root, 0)

    def replaces(self, node_id: str) -> list[str]:
        return [other for other, node in self.nodes.items() if node_id in (node.get("replaced_by") or [])]


def label(node: dict, lang: str) -> str:
    labels = node.get("labels") or {}
    return labels.get(lang) or labels.get("en") or node.get("id", "")


def tree(data: dict, *, axis: str | None = None, lang: str = "fr", deprecated: bool = False) -> str:
    index = Index(data)
    lines = [f"{data.get('title', {}).get(lang, 'taxonomy')} — version {data.get('version')}"]
    for axis_data in index.axes:
        if axis is not None and axis_data["id"] != axis:
            continue
        nodes = axis_data.get("nodes", [])
        active = sum(1 for node in nodes if node.get("status") != DEPRECATED)
        lines.append("")
        lines.append(f"{axis_data['id']} — {label(axis_data, lang)} ({active} active, {len(nodes) - active} deprecated)")
        for depth, node_id in index.walk(axis_data["id"]):
            node = index.nodes[node_id]
            retired = node.get("status") == DEPRECATED
            if retired and not deprecated:
                continue
            mark = node.get("status", "")
            if retired:
                mark += " → " + (", ".join(node.get("replaced_by") or []) or "no replacement")
            lines.append(f"{'  ' * (depth + 1)}{node_id} — {label(node, lang)} [{mark}]")
    if axis is not None and len(lines) == 1:
        raise ConfigError(f"no axis {axis}")
    return "\n".join(lines)


def show(data: dict, history: list[dict], pending: list, node_id: str) -> dict:
    index = Index(data)
    node = index.node(node_id)
    legend = (data.get("status_legend") or {}).get(node.get("status"), {})
    shown: dict = {
        "id": node_id,
        "axis": index.axis_of[node_id],
        "uri": index.uri(node_id),
        "status": node.get("status"),
        "status_means": legend.get("en") if isinstance(legend, dict) else None,
        "path": index.path(node_id),
        "parent": node.get("parent"),
        "children": index.children.get(node_id, []),
    }
    for name in ("labels", "alt_labels", "definition", "scope_note", "sources", "replaced_by"):
        if node.get(name):
            shown[name] = node[name]
    if index.replaces(node_id):
        shown["replaces"] = index.replaces(node_id)
    shown["history"] = [
        f"{entry.get('version')} {entry.get('date')} {entry.get('change')}: {entry.get('reason')}"
        for entry in history
        if entry.get("id") == node_id
    ]
    waiting = [proposal for proposal in pending if target(proposal) == node_id]
    if waiting:
        shown["pending"] = waiting[0]
    return shown


def normalize_text(text: str) -> str:
    """Lower case, no accents, words separated by single spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold().replace("ß", "ss")).split())


def find(data: dict, query: str, *, lang: str = "fr") -> list[dict]:
    """Nodes whose id, labels or alternative labels hold every word of the query.

    Ranks, best first: 3 the whole label, 2 every word inside the id or a
    label, 1 every word inside the definition or the scope note, 0 every word
    sharing its first six letters with a word of a label ("philosophie" finds
    "Philosophische Fakultät"). Accents, case and punctuation are ignored, so
    "Universitat" finds "Universität".
    """
    words = normalize_text(query).split()
    if not words:
        raise ConfigError("nothing to search for")
    whole = " ".join(words)
    stems = [word[:6] for word in words]

    def matches(score: int, plain: str) -> bool:
        if score == 3:
            return plain == whole
        if score == 0:
            return all(any(token.startswith(stem) for token in plain.split()) for stem in stems)
        return all(word in plain for word in words)

    index = Index(data)
    found = []
    for order, (node_id, node) in enumerate(index.nodes.items()):
        names = [("id", node_id.replace("_", " "))]
        names += [(f"labels.{lang_}", text) for lang_, text in (node.get("labels") or {}).items()]
        names += [
            (f"alt_labels.{lang_}", text)
            for lang_, texts in (node.get("alt_labels") or {}).items()
            for text in texts
        ]
        notes = [(f"definition.{lang_}", text) for lang_, text in (node.get("definition") or {}).items()]
        notes += [(f"scope_note.{lang_}", text) for lang_, text in (node.get("scope_note") or {}).items()]
        best = None
        for score, candidates in ((3, names), (2, names), (1, notes), (0, names)):
            for where, text in candidates:
                if matches(score, normalize_text(text)):
                    best = (score, where)
                    break
            if best:
                break
        if best:
            found.append({
                "id": node_id,
                "axis": index.axis_of[node_id],
                "label": label(node, lang),
                "status": node.get("status"),
                "match": best[1],
                "score": best[0],
                "_order": order,
            })
    found.sort(key=lambda item: (-item["score"], item["_order"]))
    for item in found:
        del item["_order"]
    return found


def history_lines(history: list[dict], node_id: str | None = None) -> list[str]:
    return [
        f"{entry.get('version')}  {entry.get('date')}  {entry.get('change'):<9}  "
        f"{entry.get('id') or '-'}: {entry.get('reason')}"
        for entry in history
        if node_id is None or entry.get("id") == node_id
    ]


def pending_rows(pending: list) -> list[dict]:
    return [
        {
            "id": target(proposal),
            "change": change_of(proposal),
            "axis": proposal.get("axis") if isinstance(proposal, dict) else None,
            "date": proposal.get("date") if isinstance(proposal, dict) else None,
            "reason": proposal.get("reason") if isinstance(proposal, dict) else None,
        }
        for proposal in pending
    ]
