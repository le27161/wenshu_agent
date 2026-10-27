"""Ontology loading and validation for enterprise semantic catalogs."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.enterprise.semantic import (
    DimensionDefinition,
    MetricDefinition,
    SemanticCatalog,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONTOLOGY_PATHS = {
    "olist": PROJECT_ROOT / "conf" / "ontology" / "olist_ecommerce.yaml",
}
_ALIAS_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.")


class OntologyError(ValueError):
    """Raised when an ontology document is invalid."""


def load_ontology_document(path: Path) -> dict[str, Any]:
    """Load an ontology YAML document from disk."""

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise OntologyError(f"ontology must be a mapping: {path}")
    return data


@lru_cache(maxsize=8)
def load_ontology_catalog(dataset: str) -> SemanticCatalog:
    """Load and validate a dataset ontology, then convert it to SemanticCatalog."""

    path = DEFAULT_ONTOLOGY_PATHS.get(dataset)
    if not path:
        raise OntologyError(f"unsupported ontology dataset: {dataset}")
    document = load_ontology_document(path)
    _validate_ontology_document(document, path)
    return _catalog_from_document(document)


def _validate_ontology_document(document: dict[str, Any], path: Path) -> None:
    required_keys = {
        "dataset",
        "entities",
        "relationships",
        "dimensions",
        "metrics",
        "filters",
        "enums",
    }
    missing = required_keys - set(document)
    if missing:
        raise OntologyError(f"{path} missing required keys: {sorted(missing)}")

    dataset = document["dataset"]
    if not isinstance(dataset, str) or not dataset:
        raise OntologyError("ontology dataset must be a non-empty string")

    entities = _mapping(document, "entities")
    relationships = _list(document, "relationships")
    dimensions = _mapping(document, "dimensions")
    metrics = _mapping(document, "metrics")
    filters = _mapping(document, "filters")
    enums = _mapping(document, "enums")

    entity_aliases: set[str] = set()
    for name, entity in entities.items():
        if not isinstance(entity, dict):
            raise OntologyError(f"entity {name} must be a mapping")
        _require(entity, ("table", "alias"), f"entity {name}")
        alias = entity["alias"]
        if alias in entity_aliases:
            raise OntologyError(f"duplicate entity alias: {alias}")
        entity_aliases.add(alias)
        _validate_alias_references(entity.get("from_sql"), entity_aliases, f"entity {name}.from_sql")

    for relationship in relationships:
        _require(relationship, ("name", "from_entity", "to_entity", "join"), "relationship")
        _require_entity(relationship["from_entity"], entities, f"relationship {relationship['name']}")
        _require_entity(relationship["to_entity"], entities, f"relationship {relationship['name']}")
        _validate_alias_references(
            relationship["join"],
            entity_aliases,
            f"relationship {relationship['name']}.join",
        )

    for name, dimension in dimensions.items():
        _require(dimension, ("source_entity", "expression"), f"dimension {name}")
        _require_entity(dimension["source_entity"], entities, f"dimension {name}")
        _validate_alias_references(
            dimension["expression"], entity_aliases, f"dimension {name}.expression"
        )

    dimension_names = set(dimensions)
    for name, metric in metrics.items():
        _require(
            metric,
            ("source_entity", "expression", "time_field", "allowed_dimensions"),
            f"metric {name}",
        )
        _require_entity(metric["source_entity"], entities, f"metric {name}")
        _validate_alias_references(metric["expression"], entity_aliases, f"metric {name}.expression")
        _validate_alias_references(metric["time_field"], entity_aliases, f"metric {name}.time_field")
        _validate_alias_references(
            metric.get("default_filter"), entity_aliases, f"metric {name}.default_filter"
        )
        allowed_dimensions = metric["allowed_dimensions"]
        if allowed_dimensions != "*":
            if not isinstance(allowed_dimensions, list):
                raise OntologyError(f"metric {name}.allowed_dimensions must be '*' or a list")
            unknown = set(allowed_dimensions) - dimension_names
            if unknown:
                raise OntologyError(
                    f"metric {name}.allowed_dimensions contains unknown dimensions: {sorted(unknown)}"
                )

    for name, filter_def in filters.items():
        _require(filter_def, ("expression", "source_entities"), f"filter {name}")
        _validate_alias_references(
            filter_def["expression"], entity_aliases, f"filter {name}.expression"
        )
        source_entities = filter_def["source_entities"]
        if not isinstance(source_entities, list) or not source_entities:
            raise OntologyError(f"filter {name}.source_entities must be a non-empty list")
        for entity_name in source_entities:
            _require_entity(entity_name, entities, f"filter {name}")

    for name, values in enums.items():
        if not isinstance(values, list):
            raise OntologyError(f"enum {name} must be a list")


def _catalog_from_document(document: dict[str, Any]) -> SemanticCatalog:
    dataset = document["dataset"]
    entities = _mapping(document, "entities")
    relationships = tuple(dict(item) for item in _list(document, "relationships"))
    dimension_documents = _mapping(document, "dimensions")
    metric_documents = _mapping(document, "metrics")
    filter_documents = _mapping(document, "filters")

    dimensions = {
        name: DimensionDefinition(
            name=name,
            expression=definition["expression"],
            source_table=entities[definition["source_entity"]]["table"],
            aliases=tuple(definition.get("aliases", ())),
            source_entity=definition["source_entity"],
        )
        for name, definition in dimension_documents.items()
    }
    all_dimension_names = tuple(dimensions)

    metrics = {}
    for name, definition in metric_documents.items():
        allowed_dimensions = definition["allowed_dimensions"]
        if allowed_dimensions == "*":
            allowed = all_dimension_names
        else:
            allowed = tuple(allowed_dimensions)
        source_entity = definition["source_entity"]
        metrics[name] = MetricDefinition(
            name=name,
            expression=definition["expression"],
            source_table=entities[source_entity]["table"],
            time_field=definition["time_field"],
            aliases=tuple(definition.get("aliases", ())),
            allowed_dimensions=allowed,
            default_filter=definition.get("default_filter"),
            description=definition.get("description", ""),
            source_entity=source_entity,
            family=definition.get("family"),
        )

    return SemanticCatalog(
        dataset=dataset,
        metrics=metrics,
        dimensions=dimensions,
        tables={entity["table"] for entity in entities.values()},
        joins=tuple(relationship["join"] for relationship in relationships),
        enum_values={
            name: {str(value) for value in values}
            for name, values in _mapping(document, "enums").items()
        },
        filter_fields=frozenset(filter_documents),
        entities={name: dict(entity) for name, entity in entities.items()},
        relationships=relationships,
        filter_expressions={
            name: definition["expression"] for name, definition in filter_documents.items()
        },
        filter_entities={
            name: frozenset(definition["source_entities"])
            for name, definition in filter_documents.items()
        },
    )


def _mapping(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise OntologyError(f"ontology key {key!r} must be a mapping")
    return value


def _list(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list):
        raise OntologyError(f"ontology key {key!r} must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise OntologyError(f"ontology key {key!r} must contain mappings")
    return value


def _require(value: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    missing = [key for key in keys if key not in value or value[key] in (None, "")]
    if missing:
        raise OntologyError(f"{label} missing required keys: {missing}")


def _require_entity(entity_name: str, entities: dict[str, Any], label: str) -> None:
    if entity_name not in entities:
        raise OntologyError(f"{label} references unknown entity: {entity_name}")


def _validate_alias_references(
    expression: str | None, aliases: set[str], label: str
) -> None:
    if not expression:
        return
    refs = set(_ALIAS_REF_RE.findall(expression))
    unknown = refs - aliases
    if unknown:
        raise OntologyError(f"{label} references unknown aliases: {sorted(unknown)}")
