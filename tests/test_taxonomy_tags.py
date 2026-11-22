from pathlib import Path

import pytest

from uzh_crawler.taxonomy import (
    REQUIRED_AXES,
    TagValidationError,
    TaxonomyError,
    load_taxonomy,
    taxonomy_from_dict,
)

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"


def test_load_real_taxonomy_has_four_axes_and_unique_ids():
    taxonomy = load_taxonomy(TAXONOMY)
    assert tuple(taxonomy.axes) == REQUIRED_AXES
    assert len(taxonomy.ids("organisation")) == 30
    assert len(taxonomy.ids("publics")) == 10
    assert len(taxonomy.ids("types_document")) == 17
    assert len(taxonomy.ids("offre_etudes")) == 9
    assert "philosophische" in taxonomy.id_set("organisation")
    assert "actualite" in taxonomy.id_set("types_document")
    assert len(taxonomy.sha256) == 64


def test_closed_tags_accept_known_ids_and_dedupe_publics():
    taxonomy = load_taxonomy(TAXONOMY)
    tags = taxonomy.validate_tags(
        {
            "organisation": "philosophische",
            "publics": ["studierende", "studierende", "studieninteressierte"],
            "types_document": "portail",
            "offre_etudes": ["bachelor", "master"],
        }
    )
    assert tags.organisation == "philosophische"
    assert tags.publics == ("studierende", "studieninteressierte")
    assert tags.types_document == "portail"
    assert tags.offre_etudes == ("bachelor", "master")
    assert tags.as_dict()["publics"] == ["studierende", "studieninteressierte"]


def test_offre_etudes_may_be_empty_or_omitted():
    taxonomy = load_taxonomy(TAXONOMY)
    payload = {
        "organisation": "philosophische",
        "publics": ["studierende"],
        "types_document": "portail",
    }
    assert taxonomy.validate_tags(payload).offre_etudes == ()
    payload["offre_etudes"] = []
    assert taxonomy.validate_tags(payload).offre_etudes == ()


def test_unknown_id_empty_publics_and_wrong_shape_are_rejected():
    taxonomy = load_taxonomy(TAXONOMY)
    base = {
        "organisation": "philosophische",
        "publics": ["studierende"],
        "types_document": "portail",
        "offre_etudes": [],
    }
    unknown = dict(base, organisation="invente")
    with pytest.raises(TagValidationError, match="invente"):
        taxonomy.validate_tags(unknown)
    with pytest.raises(TagValidationError, match="publics"):
        taxonomy.validate_tags(dict(base, publics=[]))
    with pytest.raises(TagValidationError, match="organisation"):
        taxonomy.validate_tags(dict(base, organisation=["philosophische"]))
    with pytest.raises(TagValidationError, match="offre_etudes"):
        taxonomy.validate_tags(dict(base, offre_etudes="bachelor"))


def test_duplicate_node_id_is_rejected():
    node = {"id": "universitaet", "parent": None, "labels": {}, "definition": {}, "sources": []}
    raw = {
        "schema_version": 1,
        "kind": "taxonomy",
        "axes": [
            {"id": "organisation", "nodes": [node]},
            {"id": "publics", "nodes": [dict(node, id="universitaet")]},
            {"id": "types_document", "nodes": [{"id": "portail", "parent": None}]},
            {"id": "offre_etudes", "nodes": [{"id": "bachelor", "parent": None}]},
        ],
    }
    with pytest.raises(TaxonomyError, match="duplicate node id"):
        taxonomy_from_dict(raw)
