"""Proposals: the only way taxonomy.json changes.

A proposal is one change to one node. It waits in taxonomy.proposals.json
until a person accepts or rejects it:

- ``add``: a new node. Its id is new for ever: ids are never reused.
- ``update``: new labels, alternative labels, definition, scope note or sources.
- ``move``: a new parent in the same axis.
- ``deprecate``: the node is retired, never deleted, and may name its replacements.

Accepting applies the changes, raises the version and writes one history line
per change. Rejecting removes the proposal and writes the reason to the history.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from uzh_crawler.config import ConfigError
from uzh_crawler.gate.checks import ERROR, NODE_FIELDS, check_taxonomy, errors_of
from uzh_crawler.gate.store import (
    LANGS,
    append_history,
    bump,
    dump_proposals,
    dump_taxonomy,
    history_path,
    proposals_path,
    read_history,
    read_proposals,
    read_taxonomy,
    write_text,
)
from uzh_crawler.taxonomy import DEPRECATED, REQUIRED_AXES, taxonomy_from_dict

# The version part each kind of change raises.
CHANGES = {"add": "minor", "move": "minor", "update": "patch", "deprecate": "major"}
_RANK = {"patch": 0, "minor": 1, "major": 2}
UPDATABLE = ("labels", "alt_labels", "definition", "scope_note", "sources")
_FIELDS = {
    "add": {"axis", "node"},
    "update": {"id", "set"},
    "move": {"id", "parent"},
    "deprecate": {"id", "replaced_by"},
}
_COMMON = {"change", "reason", "evidence", "date"}
_NEW_NODE = {"id", "parent", "labels", "alt_labels", "definition", "scope_note", "status", "sources"}
ACCEPTED = "accepted"
LEGENDS = {
    ACCEPTED: {
        "de": "Aus einem Crawl vorgeschlagen und von einer Person angenommen.",
        "fr": "Proposé à partir d'un crawl et accepté par une personne.",
        "en": "Proposed from a crawl and accepted by a person.",
    },
    DEPRECATED: {
        "de": "Zurückgezogen: wird nicht mehr zum Taggen verwendet. replaced_by nennt die Nachfolger.",
        "fr": "Retiré : n'est plus utilisé pour taguer. replaced_by nomme les successeurs.",
        "en": "Retired: no longer used for tagging. replaced_by names its successors.",
    },
}


class ChangeError(ConfigError):
    """A proposal that cannot enter the taxonomy. ``messages`` lists every reason."""

    def __init__(self, messages: list[str]):
        super().__init__("\n".join(messages))
        self.messages = messages


@dataclass
class Outcome:
    id: str | None
    change: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        head = f"{self.id} ({self.change})"
        return [f"{head}: {text}" for text in (*self.errors, *self.warnings)]


def change_of(proposal: object) -> str:
    return proposal.get("change", "add") if isinstance(proposal, dict) else "?"


def target(proposal: object) -> str | None:
    """The id a proposal is about: the new node's id for add, ``id`` otherwise."""
    if not isinstance(proposal, dict):
        return None
    if change_of(proposal) == "add":
        node = proposal.get("node")
        value = node.get("id") if isinstance(node, dict) else None
    else:
        value = proposal.get("id")
    return value if isinstance(value, str) else None


def shape_errors(proposal: object) -> list[str]:
    """What is wrong with the form of a proposal, before it is tried on the taxonomy."""
    if not isinstance(proposal, dict):
        return ["a proposal is a JSON object"]
    change = change_of(proposal)
    if change not in CHANGES:
        return [f"change must be add, update, move or deprecate, not {change!r}"]
    errors = []
    unknown = sorted(set(proposal) - _FIELDS[change] - _COMMON)
    if unknown:
        errors.append(f"unknown fields for {change}: {', '.join(unknown)}")
    reason = proposal.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append("reason is required: why this change, from which pages")
    evidence = proposal.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        errors.append("evidence must be a list of URLs")
    if change == "add":
        if proposal.get("axis") not in REQUIRED_AXES:
            errors.append(f"axis must be one of {', '.join(REQUIRED_AXES)}")
        node = proposal.get("node")
        if not isinstance(node, dict):
            return [*errors, "node must be an object"]
        extra = sorted(set(node) - _NEW_NODE)
        if extra:
            errors.append(f"unknown node fields: {', '.join(extra)}")
        if not isinstance(node.get("id"), str):
            errors.append("node.id is required")
        sources = node.get("sources")
        if not isinstance(sources, list) or not sources or not all(isinstance(s, str) for s in sources):
            errors.append("node.sources needs at least one URL: the unit's own page")
    else:
        if not isinstance(proposal.get("id"), str):
            errors.append("id of the node to change is required")
    if change == "update":
        values = proposal.get("set")
        if not isinstance(values, dict) or not values:
            errors.append(f"set must name at least one of {', '.join(UPDATABLE)}")
        else:
            extra = sorted(set(values) - set(UPDATABLE))
            if extra:
                errors.append(f"set can change only {', '.join(UPDATABLE)}, not {', '.join(extra)}")
            for name, value in values.items():
                if name == "sources":
                    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                        errors.append("set.sources must be a list of URLs")
                elif name in UPDATABLE and (
                    not isinstance(value, dict) or any(lang not in LANGS for lang in value)
                ):
                    errors.append(f"set.{name} maps de, fr or en to its new value")
    if change == "move":
        if "parent" not in proposal or not isinstance(proposal["parent"], (str, type(None))):
            errors.append("parent is required: an id, or null for a top node")
    if change == "deprecate":
        replaced = proposal.get("replaced_by", [])
        if not isinstance(replaced, list) or not all(isinstance(item, str) for item in replaced):
            errors.append("replaced_by must be a list of ids (empty when nothing replaces it)")
    return errors


def apply(data: dict, proposal: dict) -> dict:
    """Apply one well-formed proposal to ``data`` in place. Returns what changed."""
    change = change_of(proposal)
    index = {
        node["id"]: (axis, node)
        for axis in data["axes"]
        for node in axis.get("nodes", [])
        if isinstance(node, dict) and "id" in node
    }
    if change == "add":
        return _add(data, proposal, index)
    node_id = proposal["id"]
    if node_id not in index:
        raise ChangeError([f"no node with id {node_id}"])
    axis, node = index[node_id]
    entry = {"change": change, "id": node_id, "axis": axis["id"]}
    if change == "update":
        values = proposal["set"]
        entry["before"] = {name: copy.deepcopy(node.get(name)) for name in values}
        for name, value in values.items():
            if name == "sources":
                node["sources"] = list(value)
                continue
            merged = dict(node.get(name) or {})
            for lang, text in value.items():
                if text in ("", None, []):
                    merged.pop(lang, None)
                else:
                    merged[lang] = text
            if merged:
                node[name] = {lang: merged[lang] for lang in LANGS if lang in merged}
            else:
                node.pop(name, None)
        entry["after"] = {name: copy.deepcopy(node.get(name)) for name in values}
    elif change == "move":
        if node.get("status") == DEPRECATED:
            raise ChangeError([f"{node_id} is deprecated; a deprecated node does not move"])
        entry["before"] = {"parent": node.get("parent")}
        node["parent"] = proposal["parent"]
        entry["after"] = {"parent": node["parent"]}
    elif change == "deprecate":
        if node.get("status") == DEPRECATED:
            raise ChangeError([f"{node_id} is already deprecated"])
        replaced = list(proposal.get("replaced_by") or [])
        entry["before"] = {"status": node.get("status")}
        node["status"] = DEPRECATED
        node["replaced_by"] = replaced
        entry["after"] = {"status": DEPRECATED, "replaced_by": replaced}
        # Nodes retired earlier in favour of this one now point to its successors.
        for other_axis, other in index.values():
            if node_id in (other.get("replaced_by") or []) and other is not node:
                other["replaced_by"] = _unique(
                    item for old in other["replaced_by"] for item in (replaced if old == node_id else [old])
                )
                entry.setdefault("repointed", []).append(other["id"])
        data.setdefault("status_legend", {}).setdefault(DEPRECATED, LEGENDS[DEPRECATED])
    _reorder(node)
    return entry


def _add(data: dict, proposal: dict, index: dict) -> dict:
    source = proposal["node"]
    node_id = source["id"]
    if node_id in index:
        raise ChangeError([f"id {node_id} already exists; an id is never reused, even after deprecation"])
    axis = next((axis for axis in data["axes"] if axis.get("id") == proposal["axis"]), None)
    if axis is None:
        raise ChangeError([f"no axis {proposal['axis']}"])
    node = {name: copy.deepcopy(source[name]) for name in NODE_FIELDS if name in source}
    node.setdefault("parent", None)
    node["status"] = ACCEPTED
    _reorder(node)
    nodes = axis.setdefault("nodes", [])
    nodes.insert(_position(nodes, node.get("parent")), node)
    data.setdefault("status_legend", {}).setdefault(ACCEPTED, LEGENDS[ACCEPTED])
    return {"change": "add", "id": node_id, "axis": axis["id"], "after": copy.deepcopy(node)}


def _position(nodes: list[dict], parent: str | None) -> int:
    """Just after the last descendant of the parent, so the file reads as a tree."""
    if parent is None:
        return len(nodes)
    family = {parent}
    position = None
    for number, node in enumerate(nodes):
        if node.get("id") == parent:
            position = number + 1
        elif position is not None and node.get("parent") in family:
            family.add(node.get("id"))
            position = number + 1
    return len(nodes) if position is None else position


def _reorder(node: dict) -> None:
    ordered = {name: node[name] for name in NODE_FIELDS if name in node}
    ordered.update({name: value for name, value in node.items() if name not in ordered})
    node.clear()
    node.update(ordered)


def _unique(items) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def evaluate(data: dict, proposals: list) -> tuple[dict, list[Outcome], list[dict]]:
    """Try the proposals in order on a copy of the taxonomy.

    A proposal that fails is skipped, and the next ones are tried on what came
    before it. Only problems that a proposal brings are reported against it.
    Returns the resulting taxonomy, one outcome per proposal, and what changed.
    """
    current = copy.deepcopy(data)
    baseline = check_taxonomy(current)
    seen: set[str | None] = set()
    outcomes: list[Outcome] = []
    entries: list[dict] = []
    for proposal in proposals:
        outcome = Outcome(target(proposal), change_of(proposal), errors=shape_errors(proposal))
        outcomes.append(outcome)
        if not outcome.errors and outcome.id in seen:
            outcome.errors.append("another proposal for this node comes first: one pending change per node")
        seen.add(outcome.id)
        if outcome.errors:
            continue
        trial = copy.deepcopy(current)
        try:
            entry = apply(trial, proposal)
        except ChangeError as exc:
            outcome.errors.extend(exc.messages)
            continue
        issues = check_taxonomy(trial)
        new = [issue for issue in issues if issue not in baseline]
        outcome.errors.extend(str(issue) for issue in new if issue.level == ERROR)
        outcome.warnings.extend(str(issue) for issue in new if issue.level != ERROR)
        if not outcome.errors:
            current, baseline = trial, issues
            entries.append({**entry, "reason": proposal["reason"], "evidence": proposal.get("evidence", [])})
    return current, outcomes, entries


def pending_check(taxonomy_path: Path) -> list[Outcome]:
    data = read_taxonomy(taxonomy_path)
    pending = read_proposals(proposals_path(taxonomy_path))["proposals"]
    return evaluate(data, pending)[1]


def propose(taxonomy_path: Path, items: list, today: str) -> list[Outcome]:
    """Check new proposals and, when every one is valid, add them to the pending file."""
    data = read_taxonomy(taxonomy_path)
    path = proposals_path(taxonomy_path)
    pending_file = read_proposals(path)
    pending = pending_file["proposals"]
    waiting = {target(proposal) for proposal in pending}
    items = [_normalized(item, today) for item in items]
    outcomes = evaluate(data, [*pending, *items])[1][len(pending):]
    rejected = {
        entry.get("id"): entry
        for entry in read_history(history_path(taxonomy_path))
        if entry.get("change") == "reject"
    }
    for outcome in outcomes:
        if outcome.id in waiting:
            outcome.errors = ["a proposal for this node is already pending; reject it first"]
        if outcome.id in rejected:
            entry = rejected[outcome.id]
            outcome.warnings.append(f"a proposal for this node was rejected on {entry.get('date')}: {entry.get('reason')}")
    if not any(outcome.errors for outcome in outcomes):
        pending_file["proposals"] = [*pending, *items]
        write_text(path, dump_proposals(pending_file))
    return outcomes


def _normalized(item: object, today: str) -> object:
    if not isinstance(item, dict):
        return item
    head = {"change": change_of(item)}
    rest = {name: value for name, value in item.items() if name not in ("change", "date")}
    return {**head, **rest, "date": item.get("date") or today}


def accept(taxonomy_path: Path, ids: list[str], today: str) -> tuple[str, str, list[dict]]:
    """Apply the pending proposals for ``ids``. Returns the old and new version and the changes."""
    data = read_taxonomy(taxonomy_path)
    if errors_of(check_taxonomy(data)):
        raise ChangeError(["taxonomy.json has errors of its own: run taxonomy check and fix them first"])
    path = proposals_path(taxonomy_path)
    pending_file = read_proposals(path)
    pending = pending_file["proposals"]
    _require_pending(pending, ids)
    selected = [proposal for proposal in pending if target(proposal) in ids]
    result, outcomes, entries = evaluate(data, selected)
    problems = [line for outcome in outcomes for line in outcome.lines() if outcome.errors]
    if problems:
        raise ChangeError(problems)
    level = max((CHANGES[change_of(proposal)] for proposal in selected), key=_RANK.__getitem__)
    old = data.get("version", "")
    new = bump(old, level)
    result["version"] = new
    result["modified"] = today
    taxonomy_from_dict(result)  # classification must still be able to load it
    stamped = [{"version": new, "date": today, **entry} for entry in entries]
    write_text(taxonomy_path, dump_taxonomy(result))
    pending_file["proposals"] = [proposal for proposal in pending if target(proposal) not in ids]
    write_text(path, dump_proposals(pending_file))
    append_history(history_path(taxonomy_path), stamped)
    return old, new, stamped


def reject(taxonomy_path: Path, ids: list[str], reason: str, today: str) -> list[dict]:
    """Drop the pending proposals for ``ids`` and keep the decision in the history."""
    if not reason.strip():
        raise ConfigError("a rejection needs a reason")
    data = read_taxonomy(taxonomy_path)
    path = proposals_path(taxonomy_path)
    pending_file = read_proposals(path)
    pending = pending_file["proposals"]
    _require_pending(pending, ids)
    entries = [
        {
            "version": data.get("version"),
            "date": today,
            "change": "reject",
            "id": target(proposal),
            "reason": reason,
            "proposal": proposal,
        }
        for proposal in pending
        if target(proposal) in ids
    ]
    pending_file["proposals"] = [proposal for proposal in pending if target(proposal) not in ids]
    write_text(path, dump_proposals(pending_file))
    append_history(history_path(taxonomy_path), entries)
    return entries


def _require_pending(pending: list, ids: list[str]) -> None:
    waiting = {target(proposal) for proposal in pending}
    missing = [node_id for node_id in ids if node_id not in waiting]
    if missing:
        raise ConfigError(f"no pending proposal for: {', '.join(missing)}")
