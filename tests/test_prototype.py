"""Comprehensive unit test suite for build123d_forge.prototype.

Covers ModelPrototype, Param, ViewSpec, ValidationReport, B-Rep verification,
immutability, bounds, cross-field invariants, and introspection utilities.
"""

import unittest
from typing import Literal, Self

from build123d import Axis, Box, Compound, Face, Part, Wire
from pydantic import ValidationError, model_validator

from build123d_forge.prototype import (
    ModelPrototype,
    Param,
    ValidationReport,
    ViewSpec,
)


class DummyBox(ModelPrototype):
    """Test box prototype."""

    length: float = Param(20.0, gt=0.0, lt=100.0, unit="mm", description="Length in mm")
    width: float = Param(10.0, ge=1.0, le=50.0, unit="mm", description="Width in mm")
    height: float = Param(5.0, gt=0.0, unit="mm", description="Height in mm")
    material: Literal["PLA", "PETG", "ABS"] = Param("PLA", description="Filament material")

    def build(self) -> Part:
        b = Box(self.length, self.width, self.height)
        b.label = "dummy_box"
        return b

    def orient_for_print(self, part: Part | None = None) -> Part | None:
        target = part if part is not None else self.build()
        return target.rotate(Axis.X, 180)


class DummyZeroArgOrient(ModelPrototype):
    """Prototype with zero-arg orient_for_print hook."""

    size: float = Param(10.0, gt=0.0)

    def build(self) -> Part:
        return Box(self.size, self.size, self.size)

    def orient_for_print(self) -> Part | None:
        return self.build().rotate(Axis.Y, 90)


class DummyMultiPart(ModelPrototype):
    """Multi-part assembly prototype."""

    base_h: float = Param(15.0, gt=0.0, unit="mm")
    lid_h: float = Param(5.0, gt=0.0, unit="mm")

    def build(self) -> dict[str, Part]:
        p1 = Box(20.0, 20.0, self.base_h)
        p1.label = "base"
        p2 = Box(20.0, 20.0, self.lid_h).translate((0.0, 0.0, self.base_h))
        p2.label = "lid"
        return {"base": p1, "lid": p2}


class DummyInvariantModel(ModelPrototype):
    """Model enforcing cross-field geometric invariants."""

    wall_thickness: float = Param(3.0, gt=0.0, unit="mm")
    visor_drop: float = Param(10.0, gt=0.0, unit="mm")

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.visor_drop < 2.0 * self.wall_thickness:
            raise ValueError("visor_drop must be at least 2x wall_thickness")
        return self

    def build(self) -> Part:
        return Box(30.0, 30.0, self.visor_drop)


class DummyRequiredFieldModel(ModelPrototype):
    """Model with required parameters."""

    required_dim: float = Param(..., gt=0.0, unit="mm")
    optional_dim: float = Param(10.0, gt=0.0, unit="mm")

    def build(self) -> Part:
        return Box(self.required_dim, self.optional_dim, 5.0)


class DummyDefaultOrient(ModelPrototype):
    """Model using base orient_for_print (returns None)."""

    def build(self) -> Part:
        return Box(10.0, 10.0, 10.0)


class TestModelPrototypeInstantiation(unittest.TestCase):
    """Tests for instantiation, inheritance, and abstract method enforcement."""

    def test_cannot_instantiate_abstract_base(self):
        """Direct instantiation of ModelPrototype raises TypeError."""
        with self.assertRaises(TypeError):
            ModelPrototype()  # type: ignore

    def test_subclass_without_build_raises(self):
        """Subclass missing abstract build() raises TypeError on instantiation."""
        class IncompleteSubclass(ModelPrototype):
            dim: float = Param(10.0)

        with self.assertRaises(TypeError):
            IncompleteSubclass()  # type: ignore

    def test_subclass_with_build_instantiates(self):
        """Subclass implementing build() instantiates with default parameters."""
        inst = DummyBox()
        self.assertEqual(inst.length, 20.0)
        self.assertEqual(inst.width, 10.0)
        self.assertEqual(inst.height, 5.0)
        self.assertEqual(inst.material, "PLA")

    def test_subclass_instantiates_with_overrides(self):
        """Subclass instantiates with valid parameter overrides."""
        inst = DummyBox(length=35.0, width=15.0, material="PETG")
        self.assertEqual(inst.length, 35.0)
        self.assertEqual(inst.width, 15.0)
        self.assertEqual(inst.material, "PETG")


class TestModelPrototypeImmutability(unittest.TestCase):
    """Tests for frozen instance immutability."""

    def test_frozen_instance_modification_fails(self):
        """Modifying parameter attributes on an instance raises ValidationError."""
        inst = DummyBox()
        with self.assertRaises(ValidationError) as ctx:
            inst.length = 40.0  # type: ignore
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "frozen_instance" for e in errs))

    def test_extra_attribute_assignment_fails(self):
        """Assigning new attributes to a frozen instance raises ValidationError."""
        inst = DummyBox()
        with self.assertRaises(ValidationError):
            inst.new_attr = 100.0  # type: ignore


class TestModelPrototypeStrictFields(unittest.TestCase):
    """Tests for extra='forbid' strict field enforcement."""

    def test_extra_fields_init_rejected(self):
        """Passing undeclared keyword arguments raises ValidationError(type='extra_forbidden')."""
        with self.assertRaises(ValidationError) as ctx:
            DummyBox(length=20.0, unknown_param=42)  # type: ignore
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "extra_forbidden" for e in errs))

    def test_extra_fields_validate_rejected(self):
        """Calling model_validate with unknown keys raises ValidationError(type='extra_forbidden')."""
        with self.assertRaises(ValidationError) as ctx:
            DummyBox.model_validate({"length": 20.0, "typo_field": "test"})
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "extra_forbidden" for e in errs))


class TestParamFactory(unittest.TestCase):
    """Tests for Param metadata wrapper and bounds."""

    def test_param_preserves_default(self):
        """Param preserves explicit default value."""
        inst = DummyBox()
        self.assertEqual(inst.length, 20.0)

    def test_param_required_field_missing_raises(self):
        """Field defined with Param(...) raises ValidationError when omitted."""
        with self.assertRaises(ValidationError) as ctx:
            DummyRequiredFieldModel()  # type: ignore
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "missing" for e in errs))

    def test_param_required_field_provided_passes(self):
        """Field defined with Param(...) passes when provided."""
        inst = DummyRequiredFieldModel(required_dim=15.0)
        self.assertEqual(inst.required_dim, 15.0)
        self.assertEqual(inst.optional_dim, 10.0)

    def test_param_gt_bound_violation(self):
        """Value violating gt bound raises ValidationError(type='greater_than')."""
        with self.assertRaises(ValidationError) as ctx:
            DummyBox(length=0.0)  # gt=0.0 requires > 0
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "greater_than" for e in errs))

    def test_param_ge_bound_boundary(self):
        """Value equal to ge bound passes; value strictly less fails."""
        inst = DummyBox(width=1.0)  # ge=1.0
        self.assertEqual(inst.width, 1.0)

        with self.assertRaises(ValidationError) as ctx:
            DummyBox(width=0.99)
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "greater_than_equal" for e in errs))

    def test_param_lt_bound_violation(self):
        """Value violating lt bound raises ValidationError(type='less_than')."""
        with self.assertRaises(ValidationError) as ctx:
            DummyBox(length=100.0)  # lt=100.0 requires < 100
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "less_than" for e in errs))

    def test_param_le_bound_boundary(self):
        """Value equal to le bound passes; value strictly greater fails."""
        inst = DummyBox(width=50.0)  # le=50.0
        self.assertEqual(inst.width, 50.0)

        with self.assertRaises(ValidationError) as ctx:
            DummyBox(width=50.01)
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "less_than_equal" for e in errs))

    def test_param_unit_metadata_stored(self):
        """Param preserves unit in json_schema_extra and field.unit."""
        field = DummyBox.model_fields["length"]
        self.assertIsNotNone(field.json_schema_extra)
        self.assertEqual(field.json_schema_extra.get("unit"), "mm")
        self.assertEqual(field.unit, "mm")

    def test_param_unit_in_json_schema(self):
        """model_json_schema() includes physical unit."""
        schema = DummyBox.model_json_schema()
        self.assertEqual(schema["properties"]["length"].get("unit"), "mm")

    def test_param_without_unit_no_null(self):
        """Param without unit does not emit null unit in schema."""
        field = DummyBox.model_fields["material"]
        self.assertIsNone(field.unit)
        schema = DummyBox.model_json_schema()
        self.assertNotIn("unit", schema["properties"]["material"])

    def test_param_description_stored(self):
        """Param description is preserved in field and schema."""
        field = DummyBox.model_fields["length"]
        self.assertEqual(field.description, "Length in mm")
        schema = DummyBox.model_json_schema()
        self.assertEqual(schema["properties"]["length"].get("description"), "Length in mm")

    def test_param_literal_choices(self):
        """Param with Literal annotation restricts values to declared options."""
        inst = DummyBox(material="ABS")
        self.assertEqual(inst.material, "ABS")

        with self.assertRaises(ValidationError) as ctx:
            DummyBox(material="WOOD")  # type: ignore
        errs = ctx.exception.errors()
        self.assertTrue(any("literal_error" in e["type"] for e in errs))


class TestCrossFieldValidation(unittest.TestCase):
    """Tests for @model_validator(mode='after') cross-field geometric invariants."""

    def test_valid_invariants_pass(self):
        """Valid parameters satisfy cross-field invariant."""
        inst = DummyInvariantModel(wall_thickness=2.0, visor_drop=6.0)
        self.assertEqual(inst.wall_thickness, 2.0)
        self.assertEqual(inst.visor_drop, 6.0)

    def test_invalid_invariants_fail_validation(self):
        """Violated invariant raises ValidationError before CAD execution."""
        with self.assertRaises(ValidationError) as ctx:
            DummyInvariantModel(wall_thickness=4.0, visor_drop=6.0)  # 6.0 < 2 * 4.0 = 8.0
        self.assertIn("visor_drop must be at least 2x wall_thickness", str(ctx.exception))


class TestMethodSignatures(unittest.TestCase):
    """Tests for build(), orient_for_print(), and views() hooks."""

    def test_build_single_part(self):
        """build() returns valid B-Rep Part solid."""
        inst = DummyBox(length=30.0, width=15.0, height=8.0)
        shape = inst.build()
        self.assertIsInstance(shape, Part)
        self.assertTrue(shape.is_valid)
        self.assertTrue(shape.is_manifold)
        self.assertAlmostEqual(shape.volume, 30.0 * 15.0 * 8.0, places=2)

    def test_build_multi_part_dictionary(self):
        """build() returning dict of parts preserves individual components."""
        inst = DummyMultiPart(base_h=20.0, lid_h=6.0)
        parts = inst.build()
        self.assertIsInstance(parts, dict)
        self.assertIn("base", parts)
        self.assertIn("lid", parts)
        self.assertIsInstance(parts["base"], Part)
        self.assertIsInstance(parts["lid"], Part)

    def test_default_orient_for_print_returns_none(self):
        """Default orient_for_print() returns None."""
        inst = DummyDefaultOrient()
        self.assertIsNone(inst.orient_for_print())

    def test_custom_orient_for_print_one_arg(self):
        """Subclass 1-arg orient_for_print(part) produces transformed solid."""
        inst = DummyBox(length=20.0, width=10.0, height=5.0)
        part = inst.build()
        oriented = inst.orient_for_print(part)
        self.assertIsInstance(oriented, Part)
        self.assertTrue(oriented.is_valid)

    def test_custom_orient_for_print_zero_arg(self):
        """Subclass 0-arg orient_for_print() produces transformed solid."""
        inst = DummyZeroArgOrient(size=12.0)
        oriented = inst.orient_for_print()
        self.assertIsInstance(oriented, Part)
        self.assertTrue(oriented.is_valid)

    def test_default_views_suite(self):
        """Default views() returns 4 ViewSpec specifications."""
        inst = DummyBox()
        view_list = inst.views()
        self.assertEqual(len(view_list), 4)
        for v in view_list:
            self.assertIsInstance(v, ViewSpec)
        stems = [v.stem for v in view_list]
        self.assertIn("iso", stems)
        self.assertIn("bed", stems)
        self.assertTrue(any(v.use_print_oriented for v in view_list))


class TestViewSpec(unittest.TestCase):
    """Tests for ViewSpec camera specification model."""

    def test_viewspec_positional_arguments(self):
        """ViewSpec instantiates using positional parameters."""
        v = ViewSpec("iso_front", "Front isometric view", "front", 35.0, 25.0, False)
        self.assertEqual(v.stem, "iso_front")
        self.assertEqual(v.description, "Front isometric view")
        self.assertEqual(v.direction, "front")
        self.assertEqual(v.azimuth, 35.0)
        self.assertEqual(v.elevation, 25.0)
        self.assertFalse(v.use_print_oriented)

    def test_viewspec_keyword_arguments(self):
        """ViewSpec instantiates using keyword arguments."""
        v = ViewSpec(stem="top_view", description="Top camera", direction="top", azimuth=0.0, elevation=90.0)
        self.assertEqual(v.stem, "top_view")
        self.assertEqual(v.direction, "top")

    def test_viewspec_frozen_immutability(self):
        """Modifying ViewSpec attributes raises ValidationError."""
        v = ViewSpec("iso", "Isometric")
        with self.assertRaises(ValidationError):
            v.stem = "new_stem"  # type: ignore

    def test_viewspec_extra_forbidden(self):
        """Unknown arguments in ViewSpec raise ValidationError."""
        with self.assertRaises(ValidationError):
            ViewSpec("iso", "Isometric", invalid_extra_key=123)  # type: ignore


class TestBRepValidationGate(unittest.TestCase):
    """Tests for ModelPrototype.verify() B-Rep validation gate."""

    def test_verify_valid_solid(self):
        """verify() passes for watertight, manifold Box solid."""
        inst = DummyBox(length=30.0, width=20.0, height=10.0)
        shape = inst.build()
        rep = inst.verify(shape)
        self.assertTrue(rep.is_valid)
        self.assertTrue(rep.is_manifold)
        self.assertTrue(rep.watertight)
        self.assertEqual(rep.solids, 1)
        self.assertEqual(rep.faces, 6)
        self.assertAlmostEqual(rep.volume_mm3, 6000.0, places=2)
        self.assertEqual(rep.bounding_box_mm, (30.0, 20.0, 10.0))
        self.assertEqual(len(rep.errors), 0)

    def test_verify_empty_compound_fails(self):
        """verify() reports watertight=False and zero solids on empty Compound."""
        inst = DummyBox()
        empty = Compound()
        rep = inst.verify(empty)
        self.assertFalse(rep.watertight)
        self.assertEqual(rep.solids, 0)
        self.assertTrue(any("zero solids" in e.lower() for e in rep.errors))

    def test_verify_multi_part_dict(self):
        """verify() computes aggregated metrics across multi-part assembly."""
        inst = DummyMultiPart(base_h=10.0, lid_h=4.0)
        parts = inst.build()
        rep = inst.verify(parts)
        self.assertTrue(rep.is_valid)
        self.assertTrue(rep.is_manifold)
        self.assertTrue(rep.watertight)
        self.assertEqual(rep.solids, 2)
        expected_vol = (20.0 * 20.0 * 10.0) + (20.0 * 20.0 * 4.0)
        self.assertAlmostEqual(rep.volume_mm3, expected_vol, places=2)


class TestIntrospection(unittest.TestCase):
    """Tests for TOML template and MCP part_info extraction."""

    def test_to_toml_template(self):
        """to_toml_template generates formatted TOML preset with docs and bounds."""
        template = DummyBox.to_toml_template(schema_url="../schemas/DummyBox.schema.json")
        self.assertIn("#:schema ../schemas/DummyBox.schema.json", template)
        self.assertIn("length = 20.0", template)
        self.assertIn("[mm]", template)
        self.assertIn("Length in mm", template)
        self.assertIn("> 0.0", template)

    def test_part_info_dict(self):
        """part_info_dict derives build123d-mcp compatible parameter metadata."""
        info = DummyBox.part_info_dict()
        self.assertIn("parameters", info)
        self.assertEqual(info["description"], "Test box prototype.")
        params = info["parameters"]
        self.assertIn("length", params)
        self.assertEqual(params["length"]["type"], "float")
        self.assertEqual(params["length"]["default"], 20.0)
        self.assertEqual(params["length"]["unit"], "mm")
        self.assertEqual(params["length"]["gt"], 0.0)
        self.assertIn("material", params)
        self.assertEqual(params["material"]["choices"], ["PLA", "PETG", "ABS"])


if __name__ == "__main__":
    unittest.main()
