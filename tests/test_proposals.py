import json
import shutil
from pathlib import Path

from uzh_crawler.cli import main
from uzh_crawler.proposals import check_proposals, dump_taxonomy, unit_hints
from uzh_crawler.taxonomy import load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"


def _proposal(node_id: str = "philosophisches_seminar", **node) -> dict:
    base = {
        "id": node_id,
        "parent": "philosophische",
        "labels": {"de": "Philosophisches Seminar", "fr": "Séminaire de philosophie", "en": "Department of Philosophy"},
        "definition": {"de": "Institut der Fakultät.", "fr": "Institut de la faculté.", "en": "Institute of the faculty."},
        "status": "proposed",
        "sources": ["https://www.philosophie.uzh.ch/"],
    }
    base.update(node)
    return {"axis": "organisation", "node": base, "reason": "12 pages sans nœud propre"}


def _file(*proposals: dict) -> dict:
    return {"schema_version": 1, "kind": "taxonomy_proposals", "proposals": list(proposals)}


def test_the_repository_format_of_the_taxonomy_survives_a_rewrite():
    raw = TAXONOMY.read_text(encoding="utf-8")
    assert dump_taxonomy(json.loads(raw)) == raw


def test_check_refuses_what_could_not_enter_the_taxonomy():
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    assert check_proposals(taxonomy, _file(_proposal())) == []
    # A second proposal may hang under the first.
    child = _proposal("lehrstuhl_ethik", parent="philosophisches_seminar")
    assert check_proposals(taxonomy, _file(_proposal(), child)) == []
    assert check_proposals(taxonomy, _file(_proposal("philosophische")))
    assert check_proposals(taxonomy, _file(_proposal(parent="bachelor")))
    assert check_proposals(taxonomy, _file(_proposal(labels={"de": "Nur Deutsch"})))
    assert check_proposals(taxonomy, _file(_proposal(sources=[])))
    assert check_proposals(taxonomy, _file(_proposal("Philosophisches Seminar")))


def test_accept_moves_one_proposal_and_leaves_the_other(tmp_path: Path, capsys):
    taxonomy_path = tmp_path / "taxonomy.json"
    shutil.copy(TAXONOMY, taxonomy_path)
    proposals_path = tmp_path / "taxonomy.proposals.json"
    proposals_path.write_text(
        json.dumps(_file(_proposal(), _proposal("institut_fuer_informatik", parent="institut"))),
        encoding="utf-8",
    )
    common = ["--taxonomy", str(taxonomy_path), "--proposals", str(proposals_path)]
    assert main(["proposals", "check", *common]) == 0
    assert main(["proposals", "accept", "inconnu", *common]) == 1
    assert main(["proposals", "accept", "philosophisches_seminar", *common]) == 0
    capsys.readouterr()

    taxonomy = load_taxonomy(taxonomy_path)
    node = taxonomy.node("philosophisches_seminar")
    assert (node.parent, node.status) == ("philosophische", "accepted")
    assert len(taxonomy.ids("organisation")) == 31
    assert "accepted" in json.loads(taxonomy_path.read_text(encoding="utf-8"))["status_legend"]
    left = json.loads(proposals_path.read_text(encoding="utf-8"))["proposals"]
    assert [proposal["node"]["id"] for proposal in left] == ["institut_fuer_informatik"]


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
        ],
    }
    (tmp_path / "crawl.json").write_text(json.dumps(manifest), encoding="utf-8")
    hints = unit_hints(tmp_path)
    assert [(hint["unit"], hint["pages"]) for hint in hints] == [
        ("Philosophisches Seminar", 2),
        ("Ethik-Zentrum", 1),
    ]
    assert hints[0]["tagged_as"] == {"philosophische": 2}
    assert hints[0]["hosts"] == {"www.philosophie.uzh.ch": 2}
