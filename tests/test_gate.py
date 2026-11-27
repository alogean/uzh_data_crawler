import copy
import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from uzh_crawler.cli import main
from uzh_crawler.gate import changes
from uzh_crawler.gate.checks import RULES, check_taxonomy
from uzh_crawler.gate.export import FORMATS, export
from uzh_crawler.gate.hints import unit_hints
from uzh_crawler.gate.read import find
from uzh_crawler.gate.store import dump_taxonomy, read_history, read_proposals
from uzh_crawler.taxonomy import TagValidationError, load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"
TODAY = "2026-11-27"


def _data() -> dict:
    return json.loads(TAXONOMY.read_text(encoding="utf-8"))


def _node(data: dict, node_id: str) -> dict:
    return next(node for axis in data["axes"] for node in axis["nodes"] if node["id"] == node_id)


def _copy(tmp_path: Path) -> Path:
    path = tmp_path / "taxonomy.json"
    shutil.copy(TAXONOMY, path)
    shutil.copy(ROOT / "taxonomy.history.jsonl", tmp_path / "taxonomy.history.jsonl")
    return path


def _add(node_id: str = "philosophisches_seminar", **node) -> dict:
    base = {
        "id": node_id,
        "parent": "philosophische",
        "labels": {"de": "Philosophisches Seminar", "fr": "Séminaire de philosophie", "en": "Department of Philosophy"},
        "definition": {"de": "Seminar der Fakultät.", "fr": "Séminaire de la faculté.", "en": "Department of the faculty."},
        "sources": ["https://www.philosophie.uzh.ch/"],
    }
    base.update(node)
    return {"change": "add", "axis": "organisation", "node": base, "reason": "12 pages fell back to philosophische"}


def _cli(path: Path, *args: str) -> int:
    return main(["taxonomy", *args, "--taxonomy", str(path)])


def _propose(tmp_path: Path, path: Path, *items: dict) -> int:
    source = tmp_path / "change.json"
    source.write_text(json.dumps(list(items)), encoding="utf-8")
    return _cli(path, "propose", str(source))


# The taxonomy file and its rules ---------------------------------------------

def test_the_repository_format_of_the_taxonomy_survives_a_rewrite():
    raw = TAXONOMY.read_text(encoding="utf-8")
    assert dump_taxonomy(json.loads(raw)) == raw


def test_the_taxonomy_of_the_repository_passes_every_rule():
    assert check_taxonomy(_data()) == []


def _set(path: list, value):
    def mutate(data):
        target = _node(data, path[0]) if path[0] not in data else data
        keys = path[1:] if path[0] not in data else path
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return mutate


def _deprecate_all(axis_id):
    def mutate(data):
        data["status_legend"]["deprecated"] = {"de": "x", "fr": "x", "en": "x"}
        for node in next(axis for axis in data["axes"] if axis["id"] == axis_id)["nodes"]:
            node["status"] = "deprecated"
    return mutate


def _deprecate(node_id):
    def mutate(data):
        data["status_legend"]["deprecated"] = {"de": "x", "fr": "x", "en": "x"}
        _node(data, node_id)["status"] = "deprecated"
    return mutate


FAULTS = {
    "axes-required": lambda data: data["axes"].pop(1),
    "version-format": _set(["version"], "1.0"),
    "uri-base": _set(["uri_base"], "urn:uzh:"),
    "fields-known": _set(["mnf", "defintion"], {"en": "typo"}),
    "id-format": _set(["mnf", "id"], "Mnf Faculty"),
    "id-unique": _set(["rwf", "id"], "mnf"),
    "parent-known": _set(["mnf", "parent"], "bachelor"),
    "hierarchy-cycle": _set(["universitaet", "parent"], "fakultaeten"),
    "labels-complete": lambda data: _node(data, "mnf")["labels"].pop("fr"),
    "definition-complete": _set(["mnf", "definition", "en"], " "),
    "label-unique": lambda data: _node(data, "mnf")["labels"].update(fr="Faculté de droit"),
    "labels-disjoint": _set(["mnf", "alt_labels"], {"de": ["Mathematisch-naturwissenschaftliche Fakultät"]}),
    "status-known": _set(["mnf", "status"], "draft"),
    "replacement-valid": _set(["mnf", "replaced_by"], ["rwf"]),
    "deprecated-parent": _deprecate("fakultaeten"),
    "axis-active": _deprecate_all("offre_etudes"),
    "alt-label-clash": _set(["mnf", "alt_labels"], {"fr": ["Faculté de droit"]}),
    "sources-present": lambda data: _node(data, "mnf").update(status="accepted", sources=[])
    or data["status_legend"].update(accepted={"de": "x", "fr": "x", "en": "x"}),
}


def test_every_rule_has_a_fault_that_shows_it():
    assert set(FAULTS) == set(RULES)


@pytest.mark.parametrize("rule", sorted(FAULTS))
def test_each_rule_catches_its_fault(rule):
    data = _data()
    FAULTS[rule](data)
    assert rule in {issue.rule for issue in check_taxonomy(data)}


def test_a_cycle_is_reported_on_its_nodes_only():
    data = _data()
    _node(data, "fakultaeten")["parent"] = "mnf"
    cycle = [issue.node for issue in check_taxonomy(data) if issue.rule == "hierarchy-cycle"]
    assert sorted(cycle) == ["fakultaeten", "mnf"]


# Proposing, accepting, rejecting ---------------------------------------------

def test_propose_writes_only_what_passes_the_checks(tmp_path, capsys):
    path = _copy(tmp_path)
    pending = tmp_path / "taxonomy.proposals.json"
    assert _propose(tmp_path, path, _add(labels={"de": "Nur Deutsch"})) == 2
    assert not pending.exists()
    assert _propose(tmp_path, path, _add(parent="bachelor")) == 2
    assert _propose(tmp_path, path, _add("philosophische")) == 2
    assert _propose(tmp_path, path, _add(sources=[])) == 2
    assert _propose(tmp_path, path, {**_add(), "reason": ""}) == 2
    assert "labels-complete" in capsys.readouterr().err

    # A second proposal may hang under the first.
    child = _add("lehrstuhl_ethik", parent="philosophisches_seminar",
                 labels={"de": "Lehrstuhl Ethik", "fr": "Chaire d'éthique", "en": "Chair of Ethics"})
    assert _propose(tmp_path, path, _add(), child) == 0
    assert [p["node"]["id"] for p in read_proposals(pending)["proposals"]] == [
        "philosophisches_seminar", "lehrstuhl_ethik",
    ]
    assert read_proposals(pending)["proposals"][0]["date"]
    # One pending change per node.
    assert _propose(tmp_path, path, _add()) == 2
    assert _cli(path, "check") == 0


def test_accept_adds_a_node_raises_the_minor_version_and_writes_history(tmp_path, capsys):
    path = _copy(tmp_path)
    other = _add("institut_fuer_informatik", parent="institut",
                 labels={"de": "Institut für Informatik", "fr": "Institut d'informatique", "en": "Department of Informatics"})
    assert _propose(tmp_path, path, _add(), other) == 0
    assert _cli(path, "accept", "inconnu") == 1
    assert _cli(path, "accept", "philosophisches_seminar") == 0
    assert "version: 1.0.0 -> 1.1.0" in capsys.readouterr().out

    taxonomy = load_taxonomy(path)
    node = taxonomy.node("philosophisches_seminar")
    assert (node.parent, node.status) == ("philosophische", "accepted")
    assert len(taxonomy.ids("organisation")) == 31
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["version"], data["modified"]) == ("1.1.0", __import__("datetime").date.today().isoformat())
    ids = [node["id"] for node in next(axis for axis in data["axes"] if axis["id"] == "organisation")["nodes"]]
    assert ids.index("philosophisches_seminar") == ids.index("philosophische") + 1
    left = read_proposals(tmp_path / "taxonomy.proposals.json")["proposals"]
    assert [proposal["node"]["id"] for proposal in left] == ["institut_fuer_informatik"]
    last = read_history(tmp_path / "taxonomy.history.jsonl")[-1]
    assert (last["version"], last["change"], last["id"]) == ("1.1.0", "add", "philosophisches_seminar")
    assert check_taxonomy(data) == []


def test_a_child_cannot_be_accepted_without_its_parent(tmp_path):
    path = _copy(tmp_path)
    child = _add("lehrstuhl_ethik", parent="philosophisches_seminar",
                 labels={"de": "Lehrstuhl Ethik", "fr": "Chaire d'éthique", "en": "Chair of Ethics"})
    assert _propose(tmp_path, path, _add(), child) == 0
    assert _cli(path, "accept", "lehrstuhl_ethik") == 2
    assert _cli(path, "accept", "philosophisches_seminar", "lehrstuhl_ethik") == 0


def test_deprecate_retires_a_node_without_deleting_it(tmp_path):
    path = _copy(tmp_path)
    deprecate = {"change": "deprecate", "id": "fachbereich", "replaced_by": ["departement"],
                 "reason": "Same kind of unit as departement"}
    assert _propose(tmp_path, path, deprecate) == 0
    changes.accept(path, ["fachbereich"], TODAY)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == "2.0.0"
    node = _node(data, "fachbereich")
    assert (node["status"], node["replaced_by"]) == ("deprecated", ["departement"])
    taxonomy = load_taxonomy(path)
    assert "fachbereich" not in taxonomy.ids("organisation")
    with pytest.raises(TagValidationError, match="deprecated: fachbereich .replaced by departement"):
        taxonomy.validate_tags({"organisation": "fachbereich", "publics": ["alumni"], "types_document": "actualite"})
    # The id is never reused.
    assert _propose(tmp_path, path, _add("fachbereich", parent="types_unites")) == 2
    # When the replacement is retired in turn, the old node follows it.
    again = {"change": "deprecate", "id": "departement", "replaced_by": ["institut"], "reason": "test"}
    assert _propose(tmp_path, path, again) == 0
    changes.accept(path, ["departement"], TODAY)
    assert _node(json.loads(path.read_text(encoding="utf-8")), "fachbereich")["replaced_by"] == ["institut"]


def test_deprecate_needs_the_children_moved_first(tmp_path):
    path = _copy(tmp_path)
    retire = {"change": "deprecate", "id": "portail", "replaced_by": [], "reason": "test"}
    assert _propose(tmp_path, path, retire) == 2
    moves = [
        {"change": "move", "id": child, "parent": None, "reason": "test"}
        for child in ("studienangebot_portal", "vorlesungsverzeichnis")
    ]
    assert _propose(tmp_path, path, *moves, retire) == 0
    _, version, _ = changes.accept(path, ["studienangebot_portal", "vorlesungsverzeichnis", "portail"], TODAY)
    assert version == "2.0.0"


def test_update_changes_one_language_and_keeps_the_others(tmp_path):
    path = _copy(tmp_path)
    update = {"change": "update", "id": "mnf",
              "set": {"labels": {"en": "Faculty of Science"}, "alt_labels": {"de": ["MNF"]}},
              "reason": "Official English name"}
    assert _propose(tmp_path, path, update) == 0
    old, new, entries = changes.accept(path, ["mnf"], TODAY)
    assert (old, new) == ("1.0.0", "1.0.1")
    node = _node(json.loads(path.read_text(encoding="utf-8")), "mnf")
    assert node["labels"]["en"] == "Faculty of Science"
    assert node["labels"]["de"] == "Mathematisch-naturwissenschaftliche Fakultät"
    assert node["alt_labels"] == {"de": ["MNF"]}
    assert entries[0]["before"]["alt_labels"] is None
    assert list(node) == ["id", "parent", "labels", "alt_labels", "definition", "status", "sources"]


def test_move_refuses_a_cycle(tmp_path):
    path = _copy(tmp_path)
    assert _propose(tmp_path, path, {"change": "move", "id": "fakultaeten", "parent": "mnf", "reason": "x"}) == 2
    assert _propose(tmp_path, path, {"change": "move", "id": "mnf", "parent": "bachelor", "reason": "x"}) == 2
    assert _propose(tmp_path, path, {"change": "move", "id": "dsi", "parent": "services_centraux", "reason": "x"}) == 0
    assert changes.accept(path, ["dsi"], TODAY)[1] == "1.1.0"


def test_reject_keeps_the_reason_and_recalls_it(tmp_path, capsys):
    path = _copy(tmp_path)
    assert _propose(tmp_path, path, _add()) == 0
    assert _cli(path, "reject", "philosophisches_seminar", "--reason", "Not a unit of its own") == 0
    assert read_proposals(tmp_path / "taxonomy.proposals.json")["proposals"] == []
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "1.0.0"
    last = read_history(tmp_path / "taxonomy.history.jsonl")[-1]
    assert (last["change"], last["reason"]) == ("reject", "Not a unit of its own")
    capsys.readouterr()
    assert _propose(tmp_path, path, _add()) == 0
    assert "was rejected on" in capsys.readouterr().err


# Reading and exporting -------------------------------------------------------

def test_find_ignores_accents_and_ranks_labels_first():
    data = _data()
    assert find(data, "Universitat Zurich")[0]["id"] == "universitaet"
    assert find(data, "faculte des lettres")[0]["id"] == "philosophische"
    assert find(data, "philosophie")[0]["id"] == "philosophische"
    assert [item["id"] for item in find(data, "mnf")][:1] == ["mnf"]


def test_show_and_tree_answer_from_the_cli(tmp_path, capsys):
    path = _copy(tmp_path)
    assert _cli(path, "show", "mnf", "--json") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["path"] == ["universitaet", "fakultaeten", "mnf"]
    assert _cli(path, "show", "nothing") == 1
    assert _cli(path, "tree", "--axis", "publics") == 0
    assert "studierende_bachelor" in capsys.readouterr().out


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_export_holds_every_node(fmt):
    data = _data()
    history = read_history(ROOT / "taxonomy.history.jsonl")
    text = export(data, history, fmt, lang="fr")
    ids = [node["id"] for axis in data["axes"] for node in axis["nodes"]]
    if fmt == "csv":
        assert [row["id"] for row in csv.DictReader(io.StringIO(text))] == ids
    elif fmt == "json":
        assert [node["id"] for axis in json.loads(text)["axes"] for node in axis["nodes"]] == ids
    else:
        assert all(f"`{node_id}`" in text or f"tx:{node_id} " in text for node_id in ids)


def test_the_turtle_export_is_valid_skos():
    rdflib = pytest.importorskip("rdflib")
    data = _data()
    changed = copy.deepcopy(data)
    changed["status_legend"]["deprecated"] = {"de": "x", "fr": "x", "en": "x"}
    _node(changed, "fachbereich").update(status="deprecated", replaced_by=["departement"])
    graph = rdflib.Graph()
    graph.parse(data=export(changed, [], "ttl"), format="turtle")
    skos = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
    concepts = set(graph.subjects(rdflib.RDF.type, skos.Concept))
    assert len(concepts) == sum(len(axis["nodes"]) for axis in data["axes"])
    assert len(set(graph.subjects(rdflib.RDF.type, skos.ConceptScheme))) == 4
    retired = rdflib.URIRef(data["uri_base"] + "fachbereich")
    assert (retired, rdflib.OWL.deprecated, rdflib.Literal(True)) in graph
    assert (retired, skos.broader, None) not in graph
    for child, parent in graph.subject_objects(skos.broader):
        assert (parent, skos.narrower, child) in graph


# Hints from a run ------------------------------------------------------------

def test_hints_group_the_units_that_classification_could_not_name(tmp_path: Path):
    def page(path: str, unit: str | None, status: str = "tagged") -> dict:
        url = f"https://www.philosophie.uzh.ch/{path}"
        return {
            "url": url, "normalized_url": url, "discovered_from": None, "depth": 1, "status": status,
            "tags": {"organisation": "philosophische"}, "missing_unit": unit,
        }

    manifest = {
        "schema_version": 2, "kind": "crawl_run", "run_id": "r", "seed_url": "https://www.philosophie.uzh.ch/",
        "started_at": "2026-11-24T10:00:00+01:00", "config": {},
        "pages": [
            page("a", "Philosophisches Seminar"),
            page("b", "philosophisches  seminar"),
            page("c", None),
            page("d", "Ethik-Zentrum"),
            page("e", "Philosophisches Seminar", status="tag_error"),
            page("f", "Rechtswissenschaftliche Fakultät"),
        ],
    }
    (tmp_path / "crawl.json").write_text(json.dumps(manifest), encoding="utf-8")
    hints = unit_hints(tmp_path, _data())
    assert [(hint["unit"], hint["pages"]) for hint in hints] == [
        ("Philosophisches Seminar", 2),
        ("Ethik-Zentrum", 1),
        ("Rechtswissenschaftliche Fakultät", 1),
    ]
    assert hints[0]["tagged_as"] == {"philosophische": 2}
    assert hints[0]["hosts"] == {"www.philosophie.uzh.ch": 2}
    assert hints[0]["matches"] == []
    assert hints[2]["matches"] == ["rwf"]
