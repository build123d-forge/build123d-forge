"""JSON Schema Draft 2020-12 generation for build123d-forge prototypes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel

DRAFT_2020_12_DIALECT: str = "https://json-schema.org/draft/2020-12/schema"


def generate_json_schema(
    model_cls: type[BaseModel],
    *,
    schema_id: str | None = None,
    include_directives: bool = False,
    by_alias: bool | None = None,
    ref_template: str = "#/$defs/{model}",
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate a Draft 2020-12 compliant JSON Schema from a ModelPrototype.

    Args:
        model_cls: The ModelPrototype (or BaseModel) class to introspect.
        schema_id: Optional URI to set as the schema's '$id'.
        include_directives: If True, adds 'extends' and 'model' directives
            to allow IDE validation of sparse TOML preset files.
        by_alias: Whether to use field aliases in the schema.
        ref_template: Template string for schema references.
        **kwargs: Additional keyword arguments forwarded to Pydantic schema generation.

    Returns:
        A dictionary conforming to JSON Schema Draft 2020-12.

    Raises:
        TypeError: If model_cls is not a subclass of BaseModel.
    """
    if not isinstance(model_cls, type) or not issubclass(model_cls, BaseModel):
        raise TypeError(
            f"model_cls must be a subclass of BaseModel (e.g. ModelPrototype), got {model_cls!r}"
        )

    # Avoid infinite recursion if model_cls overrides model_json_schema
    from build123d_forge.prototype import ModelPrototype

    if issubclass(model_cls, ModelPrototype):
        base_schema = super(ModelPrototype, model_cls).model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            **kwargs,
        )
    else:
        base_schema = model_cls.model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            **kwargs,
        )

    schema: dict[str, Any] = {
        "$schema": DRAFT_2020_12_DIALECT,
    }
    if schema_id is not None:
        schema["$id"] = schema_id

    schema.update(base_schema)

    # Ensure additionalProperties: false is strictly enforced
    if "additionalProperties" not in schema:
        schema["additionalProperties"] = False

    if include_directives:
        props = schema.setdefault("properties", {})
        if "extends" not in props:
            props["extends"] = {
                "type": "string",
                "description": "Path to base configuration file to inherit parameters from",
            }
        if "model" not in props:
            props["model"] = {
                "type": "string",
                "description": "Name of the ModelPrototype class",
            }

    return schema


def serialize_json_schema(schema: dict[str, Any], *, indent: int = 2) -> str:
    """Serialize a JSON Schema dictionary to a formatted JSON string with a trailing newline.

    Args:
        schema: The JSON Schema dictionary.
        indent: Indentation spaces (default: 2).

    Returns:
        A formatted JSON string ending with a newline.
    """
    return json.dumps(schema, indent=indent) + "\n"


def export_json_schema(
    model_cls: type[BaseModel],
    output_path: Path | str,
    *,
    indent: int = 2,
    schema_id: str | None = None,
    include_directives: bool = False,
    **kwargs: Any,
) -> Path:
    """Generate and write Draft 2020-12 JSON Schema for a model to disk.

    Creates any necessary parent directories.

    Args:
        model_cls: The ModelPrototype class.
        output_path: Target file path (e.g. dist/schemas/Model.schema.json).
        indent: Indentation spaces (default: 2).
        schema_id: Optional schema $id.
        include_directives: If True, include 'extends' and 'model' property definitions.
        **kwargs: Additional arguments forwarded to schema generator.

    Returns:
        The resolved Path to the written schema file.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    schema = generate_json_schema(
        model_cls,
        schema_id=schema_id,
        include_directives=include_directives,
        **kwargs,
    )
    content = serialize_json_schema(schema, indent=indent)
    path.write_text(content, encoding="utf-8")
    return path


# Backward-compatible alias
export_schema = export_json_schema
