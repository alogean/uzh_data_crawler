"""The taxonomy in other formats: CSV, JSON, Markdown and SKOS in Turtle.

Every export is computed from taxonomy.json and its history, and is never
edited by hand. Nodes come in tree order, axis by axis.
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from uzh_crawler.gate.read import Index, label
from uzh_crawler.gate.store import LANGS
from uzh_crawler.taxonomy import DEPRECATED

FORMATS = ("csv", "json", "md", "ttl")


def export(data: dict, history: list[dict], fmt: str, *, lang: str = "fr") -> str:
    if fmt == "csv":
        return to_csv(data)
    if fmt == "json":
        return to_json(data, history)
    if fmt == "md":
        return to_markdown(data, history, lang)
    if fmt == "ttl":
        return to_turtle(data, history)
    raise ValueError(f"unknown format: {fmt}")


def _ordered(index: Index):
    """(axis id, depth, node) for every node, in tree order."""
    for axis in index.axes:
        for depth, node_id in index.walk(axis["id"]):
            yield axis["id"], depth, index.nodes[node_id]


# CSV ------------------------------------------------------------------------

CSV_COLUMNS = (
    "axis", "id", "uri", "parent", "path", "depth", "status",
    *(f"label_{lang}" for lang in LANGS),
    *(f"alt_labels_{lang}" for lang in LANGS),
    *(f"definition_{lang}" for lang in LANGS),
    *(f"scope_note_{lang}" for lang in LANGS),
    "sources", "replaced_by",
)


def to_csv(data: dict) -> str:
    """One row per node. Lists are joined with " | "; the path with " > "."""
    index = Index(data)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for axis_id, depth, node in _ordered(index):
        row = {
            "axis": axis_id,
            "id": node["id"],
            "uri": index.uri(node["id"]),
            "parent": node.get("parent") or "",
            "path": " > ".join(index.path(node["id"])),
            "depth": depth,
            "status": node.get("status", ""),
            "sources": " | ".join(node.get("sources") or []),
            "replaced_by": " | ".join(node.get("replaced_by") or []),
        }
        for lang in LANGS:
            row[f"label_{lang}"] = (node.get("labels") or {}).get(lang, "")
            row[f"alt_labels_{lang}"] = " | ".join((node.get("alt_labels") or {}).get(lang, []))
            row[f"definition_{lang}"] = (node.get("definition") or {}).get(lang, "")
            row[f"scope_note_{lang}"] = (node.get("scope_note") or {}).get(lang, "")
        writer.writerow(row)
    return out.getvalue()


# JSON -----------------------------------------------------------------------

def to_json(data: dict, history: list[dict]) -> str:
    """A flat, resolved view: each node with its URI, path and children."""
    index = Index(data)
    axes = []
    for axis in index.axes:
        nodes = []
        for depth, node_id in index.walk(axis["id"]):
            node = index.nodes[node_id]
            item = {
                "id": node_id,
                "uri": index.uri(node_id),
                "parent": node.get("parent"),
                "path": index.path(node_id),
                "depth": depth,
                "children": index.children.get(node_id, []),
                "status": node.get("status"),
            }
            for name in ("labels", "alt_labels", "definition", "scope_note", "sources", "replaced_by"):
                if node.get(name):
                    item[name] = node[name]
            nodes.append(item)
        axes.append({
            "id": axis["id"],
            "uri": index.uri(axis["id"]),
            "labels": axis.get("labels"),
            "definition": axis.get("definition"),
            "top": index.roots[axis["id"]],
            "nodes": nodes,
        })
    exported = {
        "kind": "taxonomy_export",
        "title": data.get("title"),
        "version": data.get("version"),
        "modified": data.get("modified"),
        "uri_base": data.get("uri_base"),
        "status_legend": data.get("status_legend"),
        "axes": axes,
        "history": [
            {key: entry.get(key) for key in ("version", "date", "change", "id", "reason")}
            for entry in history
        ],
    }
    return json.dumps(exported, ensure_ascii=False, indent=2) + "\n"


# Markdown -------------------------------------------------------------------

_WORDS = {
    "de": {"version": "Version", "nodes": "Knoten", "active": "aktiv", "axes": "Achsen",
           "retired": "Zurückgezogene Knoten", "node": "Knoten", "label": "Bezeichnung",
           "replaced": "Ersetzt durch", "none": "kein Ersatz", "statuses": "Status",
           "status": "Status", "means": "Bedeutung", "count": "Anzahl", "history": "Änderungen",
           "date": "Datum", "change": "Änderung", "reason": "Grund", "also": "auch"},
    "fr": {"version": "Version", "nodes": "nœuds", "active": "actifs", "axes": "axes",
           "retired": "Nœuds retirés", "node": "Nœud", "label": "Libellé",
           "replaced": "Remplacé par", "none": "sans remplaçant", "statuses": "Statuts",
           "status": "Statut", "means": "Signification", "count": "Nombre", "history": "Historique",
           "date": "Date", "change": "Changement", "reason": "Raison", "also": "aussi"},
    "en": {"version": "Version", "nodes": "nodes", "active": "active", "axes": "axes",
           "retired": "Retired nodes", "node": "Node", "label": "Label",
           "replaced": "Replaced by", "none": "no replacement", "statuses": "Statuses",
           "status": "Status", "means": "Meaning", "count": "Count", "history": "History",
           "date": "Date", "change": "Change", "reason": "Reason", "also": "also"},
}


def _cell(text: object) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def to_markdown(data: dict, history: list[dict], lang: str = "fr") -> str:
    """A document to read: one nested list per axis, then retired nodes, statuses and history."""
    words = _WORDS[lang]
    index = Index(data)
    nodes = list(index.nodes.values())
    active = [node for node in nodes if node.get("status") != DEPRECATED]
    title = (data.get("title") or {}).get(lang) or "Taxonomy"
    lines = [
        f"# {title}",
        "",
        f"{words['version']} {data.get('version')} · {data.get('modified')} · "
        f"{len(nodes)} {words['nodes']} ({len(active)} {words['active']}) · {len(index.axes)} {words['axes']}",
    ]
    provenance = (data.get("provenance") or {}).get(lang)
    if provenance:
        lines += ["", provenance]
    for axis in index.axes:
        lines += ["", f"## {label(axis, lang)} (`{axis['id']}`)", ""]
        definition = (axis.get("definition") or {}).get(lang)
        if definition:
            lines += [definition, ""]
        for depth, node_id in index.walk(axis["id"]):
            node = index.nodes[node_id]
            if node.get("status") == DEPRECATED:
                continue
            text = f"{'  ' * depth}- **{label(node, lang)}** `{node_id}` *{node.get('status', '')}*"
            alt = (node.get("alt_labels") or {}).get(lang)
            if alt:
                text += f" ({words['also']}: {', '.join(alt)})"
            node_definition = (node.get("definition") or {}).get(lang)
            if node_definition:
                text += f" — {node_definition}"
            lines.append(text)
    retired = [node for node in nodes if node.get("status") == DEPRECATED]
    if retired:
        lines += ["", f"## {words['retired']}", "",
                  f"| {words['node']} | {words['label']} | {words['replaced']} |", "| --- | --- | --- |"]
        for node in retired:
            replaced = ", ".join(f"`{item}`" for item in node.get("replaced_by") or []) or words["none"]
            lines.append(f"| `{node['id']}` | {_cell(label(node, lang))} | {replaced} |")
    legend = data.get("status_legend") or {}
    if legend:
        lines += ["", f"## {words['statuses']}", "",
                  f"| {words['status']} | {words['means']} | {words['count']} |", "| --- | --- | --- |"]
        for status, meaning in legend.items():
            count = sum(1 for node in nodes if node.get("status") == status)
            text = meaning.get(lang) if isinstance(meaning, dict) else meaning
            lines.append(f"| `{status}` | {_cell(text)} | {count} |")
    if history:
        lines += ["", f"## {words['history']}", "",
                  f"| {words['version']} | {words['date']} | {words['change']} | {words['node']} | {words['reason']} |",
                  "| --- | --- | --- | --- | --- |"]
        for entry in history:
            node = f"`{entry['id']}`" if entry.get("id") else ""
            lines.append(
                f"| {entry.get('version')} | {entry.get('date')} | {entry.get('change')} | {node} | "
                f"{_cell(entry.get('reason'))} |"
            )
    return "\n".join(lines) + "\n"


# SKOS in Turtle -------------------------------------------------------------

def _literal(text: str, lang: str | None = None) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"' + (f"@{lang}" if lang else "")


def _iri(url: str) -> str:
    return "<" + quote(url, safe=":/?#[]@!$&'()*+,;=%~-._") + ">"


def _texts(prop: str, values: dict | None) -> list[str]:
    items = [_literal(values[lang], lang) for lang in LANGS if values and values.get(lang)]
    return [f"{prop} {', '.join(items)}"] if items else []


def _block(subject: str, kind: str, statements: list[str]) -> str:
    body = " ;\n    ".join([f"a {kind}", *statements])
    return f"{subject} {body} ."


def to_turtle(data: dict, history: list[dict]) -> str:
    """SKOS: each axis a skos:ConceptScheme, each node a skos:Concept.

    The id is the concept's skos:notation and the end of its URI. A deprecated
    node stays a concept, marked owl:deprecated, with dct:isReplacedBy, and
    leaves the hierarchy. The history of a node becomes its skos:changeNote.
    """
    index = Index(data)
    base = data.get("uri_base", "")
    version = data.get("version", "")
    modified = data.get("modified")
    legend = data.get("status_legend") or {}
    title = (data.get("title") or {}).get("en", "taxonomy")
    lines = [
        f"# {title}, version {version}",
        "# Generated by `python -m uzh_crawler taxonomy export --format ttl`. Do not edit by hand.",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix dct: <http://purl.org/dc/terms/> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        f"@prefix tx: <{base}> .",
        "",
    ]

    def active(node_id: str) -> bool:
        return index.nodes[node_id].get("status") != DEPRECATED

    for axis in index.axes:
        tops = [f"tx:{node_id}" for node_id in index.roots[axis["id"]] if active(node_id)]
        statements = [
            *_texts("skos:prefLabel", axis.get("labels")),
            *_texts("skos:definition", axis.get("definition")),
            f"owl:versionInfo {_literal(version)}",
        ]
        if modified:
            statements.append(f'dct:modified "{modified}"^^xsd:date')
        if tops:
            statements.append(f"skos:hasTopConcept {', '.join(tops)}")
        lines += [_block(f"tx:{axis['id']}", "skos:ConceptScheme", statements), ""]

    for axis_id, _depth, node in _ordered(index):
        node_id = node["id"]
        retired = node.get("status") == DEPRECATED
        statements = [f"skos:inScheme tx:{axis_id}", f"skos:notation {_literal(node_id)}"]
        if not retired:
            if node.get("parent") is None:
                statements.append(f"skos:topConceptOf tx:{axis_id}")
            else:
                statements.append(f"skos:broader tx:{node['parent']}")
            narrower = [f"tx:{child}" for child in index.children.get(node_id, []) if active(child)]
            if narrower:
                statements.append(f"skos:narrower {', '.join(narrower)}")
        statements += _texts("skos:prefLabel", node.get("labels"))
        alt = node.get("alt_labels") or {}
        alt_items = [_literal(text, lang) for lang in LANGS for text in alt.get(lang, [])]
        if alt_items:
            statements.append(f"skos:altLabel {', '.join(alt_items)}")
        statements += _texts("skos:definition", node.get("definition"))
        statements += _texts("skos:scopeNote", node.get("scope_note"))
        meaning = legend.get(node.get("status"))
        if isinstance(meaning, dict):
            statements += _texts("skos:editorialNote", meaning)
        notes = [
            _literal(f"{entry.get('version')} ({entry.get('date')}) {entry.get('change')}: {entry.get('reason')}", "en")
            for entry in history
            if entry.get("id") == node_id and entry.get("change") != "reject"
        ]
        if notes:
            statements.append(f"skos:changeNote {', '.join(notes)}")
        if node.get("sources"):
            statements.append(f"dct:source {', '.join(_iri(url) for url in node['sources'])}")
        if retired:
            statements.append("owl:deprecated true")
            if node.get("replaced_by"):
                statements.append(f"dct:isReplacedBy {', '.join(f'tx:{item}' for item in node['replaced_by'])}")
        lines += [_block(f"tx:{node_id}", "skos:Concept", statements), ""]
    return "\n".join(lines)
