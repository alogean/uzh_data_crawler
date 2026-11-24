from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from uzh_crawler.taxonomy import REQUIRED_AXES, TagValidationError, Taxonomy

_MODELS: dict[tuple, type[BaseModel]] = {}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


def annotation_model(taxonomy: Taxonomy) -> type[BaseModel]:
    """Pydantic model whose tag fields are closed enums of taxonomy ids."""
    key = tuple(taxonomy.ids(axis_id) for axis_id in REQUIRED_AXES)
    cached = _MODELS.get(key)
    if cached is not None:
        return cached
    for axis_id, ids in zip(REQUIRED_AXES, key, strict=True):
        if not ids:
            raise TagValidationError(f"{axis_id} axis is empty")
    organisation = Literal.__getitem__(key[0])
    public = Literal.__getitem__(key[1])
    type_document = Literal.__getitem__(key[2])
    offre = Literal.__getitem__(key[3])
    model = create_model(
        "PageTags",
        __base__=_Closed,
        organisation=(organisation, ...),
        publics=(list[public], Field(min_length=1)),
        types_document=(type_document, ...),
        offre_etudes=(list[offre], Field(default_factory=list)),
        # Free text, not a tag: the unit that has no node of its own yet.
        unite_manquante=(str | None, Field(default=None, max_length=200)),
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


def missing_unit_of(annotation: BaseModel) -> str | None:
    value = (getattr(annotation, "unite_manquante", None) or "").strip()
    return value or None


def parse_annotation(data: dict, taxonomy: Taxonomy) -> BaseModel:
    model = annotation_model(taxonomy)
    try:
        annotation = model.model_validate(data)
    except ValidationError as exc:
        raise TagValidationError(str(exc)) from exc
    taxonomy.validate_tags(tags_of(annotation))
    return annotation
