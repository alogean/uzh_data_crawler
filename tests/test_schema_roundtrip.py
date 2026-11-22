import json
from pathlib import Path

import pytest

from uzh_crawler.digest.prompt import system_prompt, truncate_for_llm
from uzh_crawler.digest.schema import annotation_model, parse_annotation
from uzh_crawler.taxonomy import TagValidationError, load_taxonomy

ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = ROOT / "taxonomy.json"
FIXTURE = ROOT / "tests" / "fixtures" / "llm_page.json"


def test_fixture_json_roundtrips_through_the_closed_schema():
    taxonomy = load_taxonomy(TAXONOMY)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    annotation = parse_annotation(json.dumps(payload), taxonomy)
    fenced = parse_annotation("```json\n" + json.dumps(payload) + "\n```", taxonomy)
    assert annotation.organisation == "philosophische"
    assert list(annotation.publics) == ["studierende", "studieninteressierte"]
    assert annotation.types_document == "portail"
    assert list(annotation.offre_etudes) == ["bachelor", "master"]
    assert fenced.title == "Studium"
    assert "cursus" in fenced.resume_markdown


def test_schema_rejects_unknown_ids_and_extra_fields():
    taxonomy = load_taxonomy(TAXONOMY)
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(TagValidationError):
        parse_annotation(json.dumps(dict(payload, organisation="invente")), taxonomy)
    with pytest.raises(TagValidationError):
        parse_annotation(json.dumps(dict(payload, publics=[])), taxonomy)
    with pytest.raises(TagValidationError):
        parse_annotation(json.dumps(dict(payload, note="hors schéma")), taxonomy)


def test_json_schema_lists_only_taxonomy_ids():
    taxonomy = load_taxonomy(TAXONOMY)
    schema = annotation_model(taxonomy).model_json_schema()
    encoded = json.dumps(schema)
    assert "philosophische" in encoded
    assert "studierende" in encoded
    assert "portail" in encoded
    assert "bachelor" in encoded
    assert "invente" not in encoded
    assert schema["properties"]["publics"].get("minItems") == 1
    prompt = system_prompt(taxonomy, "fr")
    assert "philosophische —" in prompt
    assert "uniquement" in prompt


def test_truncate_keeps_short_text_and_caps_long_text():
    assert truncate_for_llm("bonjour") == "bonjour"
    long = "# Titre\n" + ("mot " * 30_000)
    assert len(long) > 80_000
    shortened = truncate_for_llm(long)
    assert len(shortened) <= 80_000
    assert "# Titre" in shortened
    assert "Table des matières extraite" in shortened
