# -*- coding: utf-8 -*-
"""A small, closed subset of JSON Schema for skill tool arguments and results.

Only the keywords below are understood. A manifest that uses any other keyword
is rejected at install time instead of being validated loosely, because a
keyword the validator ignores is a restriction the author believes holds and
the core never enforces.
"""
from __future__ import annotations

from typing import Any

SUPPORTED_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "enum",
    "minimum", "maximum", "minLength", "maxLength", "items", "minItems",
    "maxItems", "description", "default", "title",
})
_TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object", "null"})
MAX_SCHEMA_DEPTH = 8
MAX_ERRORS = 20


class SchemaError(ValueError):
    """The schema itself uses something this subset does not support."""


def check_schema(schema: Any, *, where: str = "$", depth: int = 0) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise SchemaError(f"{where}: schema nesting is deeper than {MAX_SCHEMA_DEPTH}")
    if not isinstance(schema, dict):
        raise SchemaError(f"{where}: a schema must be an object")
    unknown = set(schema) - SUPPORTED_KEYWORDS
    if unknown:
        raise SchemaError(f"{where}: unsupported keywords {sorted(unknown)}")
    kinds = _declared_types(schema, where)
    for key in ("minimum", "maximum"):
        if key in schema and not _is_number(schema[key]):
            raise SchemaError(f"{where}.{key} must be a number")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and not (isinstance(schema[key], int) and not isinstance(schema[key], bool) and schema[key] >= 0):
            raise SchemaError(f"{where}.{key} must be a non-negative integer")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError(f"{where}.enum must be a non-empty list")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise SchemaError(f"{where}.required must be a list of strings")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
        raise SchemaError(f"{where}.additionalProperties must be true or false")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise SchemaError(f"{where}.properties must be an object")
    for name, child in properties.items():
        check_schema(child, where=f"{where}.properties.{name}", depth=depth + 1)
    if "items" in schema:
        check_schema(schema["items"], where=f"{where}.items", depth=depth + 1)
    if properties and kinds and "object" not in kinds:
        raise SchemaError(f"{where}: properties given for a non-object type")


def validate(value: Any, schema: dict[str, Any]) -> list[str]:
    """Every violation of ``schema`` by ``value``, as readable paths; empty when valid."""
    errors: list[str] = []
    _validate(value, schema, "$", errors)
    return errors[:MAX_ERRORS]


def _validate(value: Any, schema: dict[str, Any], where: str, errors: list[str]) -> None:
    if len(errors) >= MAX_ERRORS:
        return
    kinds = _declared_types(schema, where)
    if kinds and not any(_has_type(value, kind) for kind in kinds):
        errors.append(f"{where}: expected {' or '.join(sorted(kinds))}, got {_type_name(value)}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {schema['enum']}")
    if isinstance(value, str):
        _check_bounds(len(value), schema, "minLength", "maxLength", where, "characters", errors)
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: must be <= {schema['maximum']}")
    if isinstance(value, list):
        _check_bounds(len(value), schema, "minItems", "maxItems", where, "items", errors)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{where}[{index}]", errors)
    if isinstance(value, dict):
        _validate_object(value, schema, where, errors)


def _validate_object(value: dict[str, Any], schema: dict[str, Any], where: str, errors: list[str]) -> None:
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{where}: missing required field {name!r}")
    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in properties:
                errors.append(f"{where}: unexpected field {name!r}")
    for name, child in properties.items():
        if name in value:
            _validate(value[name], child, f"{where}.{name}", errors)


def _check_bounds(
    size: int, schema: dict[str, Any], low_key: str, high_key: str, where: str, unit: str, errors: list[str]
) -> None:
    if low_key in schema and size < schema[low_key]:
        errors.append(f"{where}: at least {schema[low_key]} {unit}")
    if high_key in schema and size > schema[high_key]:
        errors.append(f"{where}: at most {schema[high_key]} {unit}")


def _declared_types(schema: dict[str, Any], where: str) -> set[str]:
    declared = schema.get("type")
    if declared is None:
        return set()
    kinds = {declared} if isinstance(declared, str) else set(declared) if isinstance(declared, list) else None
    if not kinds or not kinds <= _TYPES:
        raise SchemaError(f"{where}.type must name JSON types from {sorted(_TYPES)}")
    return kinds


def _has_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return _is_number(value)
    return {
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        "null": value is None,
    }[kind]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(value), type(value).__name__)
