"""Unit test suite for build123d_forge.schema.

Validates JSON Schema Draft 2020-12 dialect compliance, physical unit metadata,
property constraints, IDE directive injections, serialization, and file exports.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, Field

from build123d_forge.prototype import ModelPrototype, Param
from build123d_forge.schema import (
    DRAFT_2020_12_DIALECT,
    export_json_schema,
    generate_json_schema,
    serialize_json_schema,
)


class SampleBoxModel(ModelPrototype):
    """Parametric test box schema definition."""

    length: float = Param(20.0, gt=0.0, lt=100.0, unit="mm", description="Length in mm")
    count: int = Param(4, ge=1, le=10, description="Hole count")
    wall_gap: float = Param(9.625, gt=0.0, unit="mm", description="Wall gap")
    material: Literal["PLA", "PETG", "ABS"] = Param("PLA", description="Material type")
    enabled: bool = Param(True, description="Enable feature")
    required_id: str = Param(..., description="Part identifier")

    def build(self):
        from build123d import Box
        return Box(self.length, self.wall_gap, 10.0)


class TestSchemaGeneration(unittest.TestCase):
    """Tests for generate_json_schema function and Draft 2020-12 compliance."""

    def test_meta_schema_compliance(self):
        """Emitted schema validates cleanly against Draft 2020-12 meta-schema."""
        schema = generate_json_schema(SampleBoxModel)
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_dialect_header(self):
        """Schema includes official Draft 2020-12 $schema dialect URI."""
        schema = generate_json_schema(SampleBoxModel)
        self.assertEqual(schema["$schema"], DRAFT_2020_12_DIALECT)

    def test_schema_id_optional(self):
        """$id is included when provided, and omitted when None."""
        schema_with_id = generate_json_schema(SampleBoxModel, schema_id="https://example.com/schema.json")
        self.assertEqual(schema_with_id["$id"], "https://example.com/schema.json")

        schema_no_id = generate_json_schema(SampleBoxModel)
        self.assertNotIn("$id", schema_no_id)

    def test_root_description_from_docstring(self):
        """Model docstring is extracted as root schema description."""
        schema = generate_json_schema(SampleBoxModel)
        self.assertEqual(schema["description"], "Parametric test box schema definition.")

    def test_additional_properties_forbidden(self):
        """Root schema explicitly enforces additionalProperties: false."""
        schema = generate_json_schema(SampleBoxModel)
        self.assertIs(schema["additionalProperties"], False)

    def test_float_and_int_types(self):
        """Float maps to 'number' and int maps to 'integer'."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["length"]["type"], "number")
        self.assertEqual(props["count"]["type"], "integer")

    def test_string_and_bool_types(self):
        """Str maps to 'string' and bool maps to 'boolean'."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["required_id"]["type"], "string")
        self.assertEqual(props["enabled"]["type"], "boolean")

    def test_literal_enum_type(self):
        """Literal annotations map to enum values."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["material"]["type"], "string")
        self.assertEqual(props["material"]["enum"], ["PLA", "PETG", "ABS"])

    def test_numeric_bounds_mapping(self):
        """gt, ge, lt, le map to Draft 2020-12 minimum/maximum constraints."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["length"]["exclusiveMinimum"], 0.0)
        self.assertEqual(props["length"]["exclusiveMaximum"], 100.0)
        self.assertEqual(props["count"]["minimum"], 1)
        self.assertEqual(props["count"]["maximum"], 10)

    def test_unit_metadata_embedding(self):
        """Physical unit metadata is embedded directly into property schemas."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["length"]["unit"], "mm")
        self.assertEqual(props["wall_gap"]["unit"], "mm")
        self.assertNotIn("unit", props["count"])

    def test_default_value_preservation(self):
        """Field defaults are preserved in property schemas."""
        schema = generate_json_schema(SampleBoxModel)
        props = schema["properties"]
        self.assertEqual(props["length"]["default"], 20.0)
        self.assertEqual(props["count"]["default"], 4)
        self.assertEqual(props["enabled"]["default"], True)
        self.assertNotIn("default", props["required_id"])

    def test_required_field_handling(self):
        """Only fields without defaults are listed in 'required'."""
        schema = generate_json_schema(SampleBoxModel)
        self.assertIn("required", schema)
        self.assertIn("required_id", schema["required"])
        self.assertNotIn("length", schema["required"])
        self.assertNotIn("count", schema["required"])

    def test_include_directives_flag(self):
        """include_directives=True injects extends and model properties for TOML presets."""
        schema_with_directives = generate_json_schema(SampleBoxModel, include_directives=True)
        props = schema_with_directives["properties"]
        self.assertIn("extends", props)
        self.assertEqual(props["extends"]["type"], "string")
        self.assertIn("model", props)
        self.assertEqual(props["model"]["type"], "string")

        schema_without = generate_json_schema(SampleBoxModel, include_directives=False)
        self.assertNotIn("extends", schema_without["properties"])
        self.assertNotIn("model", schema_without["properties"])

    def test_type_error_on_non_model(self):
        """Passing a non-model class or object raises TypeError."""
        with self.assertRaises(TypeError):
            generate_json_schema(dict)  # type: ignore

        with self.assertRaises(TypeError):
            generate_json_schema(123)  # type: ignore


class TestInstanceValidation(unittest.TestCase):
    """Tests validating data dictionaries against the generated JSON schema."""

    def setUp(self):
        self.schema = generate_json_schema(SampleBoxModel)
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def test_instance_validation_sparse_config(self):
        """Sparse configuration dictionary validates cleanly."""
        data = {"required_id": "box-01", "length": 35.0}
        self.validator.validate(data)

    def test_instance_validation_bounds_failure(self):
        """Value violating exclusiveMinimum raises ValidationError."""
        data = {"required_id": "box-01", "length": -5.0}
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(data)

    def test_instance_validation_enum_failure(self):
        """Value outside Literal enum choices raises ValidationError."""
        data = {"required_id": "box-01", "material": "NYLON"}
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(data)

    def test_instance_validation_extra_forbidden(self):
        """Unknown extra key raises ValidationError due to additionalProperties: false."""
        data = {"required_id": "box-01", "unknown_key": "val"}
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(data)


class TestSchemaSerializationAndExport(unittest.TestCase):
    """Tests for serialize_json_schema and export_json_schema."""

    def test_serialize_json_schema(self):
        """serialize_json_schema produces valid indented JSON string ending with a newline."""
        schema = generate_json_schema(SampleBoxModel)
        text = serialize_json_schema(schema, indent=2)
        self.assertTrue(text.endswith("\n"))
        parsed = json.loads(text)
        self.assertEqual(parsed["$schema"], DRAFT_2020_12_DIALECT)

    def test_export_json_schema_file(self):
        """export_json_schema writes schema to file and creates directories."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "schemas" / "nested" / "SampleBox.schema.json"
            exported_path = export_json_schema(SampleBoxModel, out_file)
            self.assertEqual(exported_path, out_file.resolve())
            self.assertTrue(out_file.exists())

            content = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(content["$schema"], DRAFT_2020_12_DIALECT)
            self.assertIn("length", content["properties"])


if __name__ == "__main__":
    unittest.main()
