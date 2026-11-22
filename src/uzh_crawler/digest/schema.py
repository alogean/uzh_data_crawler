from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from typing import Literal

from uzh_crawler.taxonomy import TagValidationError, Taxonomy

_MODELS: dict[tuple, type[BaseModel]] = {}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


def annotation_model(taxonomy: Taxonomy) -> type[BaseModel]:
    """Pydantic model whose tag fields are closed enums of taxonomy ids."""
    key = (
        taxonomy.ids("organisation"),
        taxonomy.ids("publics"),
        taxonomy.ids("types_document"),
        taxonomy.ids("offre_etudes"),
    )
    cached = _MODELS.get(key)
    if cached is not None:
        return cached
    for axis_id, ids in zip(
        ("organisation", "publics", "types_document", "offre_etudes"),
        key,
        strict=True,
    ):
        if not ids:
            raise TagValidationError(f"{axis_id} axis is empty")
    organisation = Literal.__getitem__(key[0])
    public = Literal.__getitem__(key[1])
    type_document = Literal.__getitem__(key[2])
    offre = Literal.__getitem__(key[3])
    model = create_model(
        "PageAnnotation",
        __base__=_Closed,
        organisation=(organisation, ...),
        publics=(list[public], Field(min_length=1)),
        types_document=(type_document, ...),
        offre_etudes=(list[offre], Field(default_factory=list)),
        title=(str, Field(min_length=1)),
        resume_markdown=(str, Field(min_length=1)),
    )
    _MODELS[key] = model
    return model


def tags_of(annotation: BaseModel) -> dict:
    return {
        "organisation": annotation.organisation,
        "publics": list(annotation.publics),
        "types_document": annotation.types_document,
        "offre_etudes": list(annotation.offre_etudes),
    }


def parse_annotation(payload: str, taxonomy: Taxonomy) -> BaseModel:
    data = _load_json_object(payload)
    model = annotation_model(taxonomy)
    try:
        annotation = model.model_validate(data)
    except ValidationError as exc:
        raise TagValidationError(str(exc)) from exc
    taxonomy.validate_tags(tags_of(annotation))
    return annotation


def _load_json_object(payload: str) -> dict:
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise TagValidationError("model output is not a JSON object")
    return data
