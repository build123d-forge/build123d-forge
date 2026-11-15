"""Unit tests for build123d_forge.mcp_shim.

Validates build123d-mcp backward compatibility, runtime and static AST PART_INFO
generation, make() and make_print_oriented() injection, and builtins hooks.
"""

import ast
import unittest
from typing import Literal

from build123d import Axis, Box, Compound, Part
from pydantic import BaseModel, Field, ValidationError

from build123d_forge.mcp_shim import (
    generate_part_info,
    generate_part_info_ast,
    generate_part_info_code,
    mcp_shim,
)
from build123d_forge.prototype import ModelPrototype, Param


class DummyBoxModel(ModelPrototype):
    """Parametric test box."""

    __tags__ = ["test", "sample"]

    length: float = Param(10.0, gt=0.0, unit="mm", description="Box length")
    width: float = Param(5.0, gt=0.0, unit="mm", description="Box width")
    height: float = Param(2.0, gt=0.0, unit="mm", description="Box height")
    material: Literal["pla", "petg"] = Param("pla", description="Material type")

    def build(self) -> Part:
        return Box(self.length, self.width, self.height)

    def orient_for_print(self, part: Part | None = None) -> Part | None:
        target = part if part is not None else self.build()
        return target.rotate(Axis.X, 180)


class DummyMultiPartModel(ModelPrototype):
    """Parametric two-part assembly."""

    size: float = Param(10.0, gt=0.0)

    def build(self) -> dict[str, Part]:
        return {"b1": Box(self.size, self.size, self.size), "b2": Box(self.size, self.size, 2.0)}

    def orient_for_print(self, part: Part | None = None) -> None:
        return None


class TestMcpShim(unittest.TestCase):
    """Tests for mcp_shim interoperability bridge."""

    def test_shim_invalid_cls(self):
        """mcp_shim raises TypeError when passed a non-class or invalid type."""
        with self.assertRaises(TypeError):
            mcp_shim(dict, {})  # type: ignore

        with self.assertRaises(TypeError):
            mcp_shim("NotAClass", {})  # type: ignore

    def test_shim_invalid_globals(self):
        """mcp_shim raises TypeError when module_globals is not a dict."""
        with self.assertRaises(TypeError):
            mcp_shim(DummyBoxModel, "not-a-dict")  # type: ignore

    def test_part_info_generation(self):
        """generate_part_info derives parameters, types, units, defaults, and bounds."""
        info = generate_part_info(DummyBoxModel)
        self.assertEqual(info["description"], "Parametric test box.")
        self.assertEqual(info["tags"], ["test", "sample"])
        params = info["parameters"]
        self.assertIn("length", params)
        self.assertEqual(params["length"]["type"], "float")
        self.assertEqual(params["length"]["default"], 10.0)
        self.assertEqual(params["length"]["unit"], "mm")
        self.assertEqual(params["length"]["gt"], 0.0)
        self.assertIn("material", params)
        self.assertEqual(params["material"]["type"], "str")
        self.assertEqual(params["material"]["default"], "pla")
        self.assertEqual(params["material"]["choices"], ["pla", "petg"])

    def test_part_info_static_ast(self):
        """generate_part_info_code and ast node evaluate via ast.literal_eval cleanly."""
        code = generate_part_info_code(DummyBoxModel)
        self.assertTrue(code.startswith("PART_INFO = {"))

        assign_node = generate_part_info_ast(DummyBoxModel)
        self.assertIsInstance(assign_node, ast.Assign)
        extracted = ast.literal_eval(assign_node.value)
        self.assertEqual(extracted["description"], "Parametric test box.")
        self.assertIn("length", extracted["parameters"])
        self.assertEqual(extracted["parameters"]["length"]["default"], 10.0)

    def test_globals_injection(self):
        """mcp_shim injects PART_INFO, make, and make_print_oriented into globals."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        self.assertIn("PART_INFO", mod_globals)
        self.assertIn("make", mod_globals)
        self.assertIn("make_print_oriented", mod_globals)
        self.assertTrue(callable(mod_globals["make"]))
        self.assertTrue(callable(mod_globals["make_print_oriented"]))

    def test_inject_defaults_option(self):
        """inject_defaults=True injects default parameter values into module globals."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals, inject_defaults=True)
        self.assertIn("length", mod_globals)
        self.assertEqual(mod_globals["length"], 10.0)
        self.assertIn("material", mod_globals)
        self.assertEqual(mod_globals["material"], "pla")

    def test_make_default_call(self):
        """make() instantiates defaults and returns valid solid."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        part = mod_globals["make"]()
        self.assertIsInstance(part, Part)
        self.assertTrue(part.is_valid)
        self.assertAlmostEqual(part.volume, 100.0, places=1)

    def test_make_with_kwargs(self):
        """make(**kwargs) applies valid parameter overrides."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        part = mod_globals["make"](length=20.0)
        self.assertAlmostEqual(part.volume, 200.0, places=1)

    def test_make_validation_error_on_bound(self):
        """make() propagates ValidationError on bound violations."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        with self.assertRaises(ValidationError):
            mod_globals["make"](length=-10.0)

    def test_make_validation_error_on_extra_keys(self):
        """make() propagates ValidationError on unknown parameters."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        with self.assertRaises(ValidationError):
            mod_globals["make"](unknown_key="val")

    def test_make_multipart_assembly(self):
        """make() wraps multi-part build dict into a Compound."""
        mod_globals = {"__name__": "models.assembly"}
        mcp_shim(DummyMultiPartModel, mod_globals)
        assembly = mod_globals["make"]()
        self.assertIsInstance(assembly, (Compound, Part))
        self.assertEqual(len(assembly.solids()), 2)

    def test_make_print_oriented(self):
        """make_print_oriented() applies orient_for_print."""
        mod_globals = {"__name__": "models.box"}
        mcp_shim(DummyBoxModel, mod_globals)
        po = mod_globals["make_print_oriented"]()
        self.assertIsInstance(po, Part)
        self.assertTrue(po.is_valid)

    def test_make_print_oriented_fallback(self):
        """make_print_oriented() falls back to upright shape when orient_for_print returns None."""
        mod_globals = {"__name__": "models.assembly"}
        mcp_shim(DummyMultiPartModel, mod_globals)
        po = mod_globals["make_print_oriented"]()
        self.assertEqual(len(po.solids()), 2)

    def test_builtins_hook_eager(self):
        """When __name__ == 'builtins', result is eagerly assigned to make()."""
        mod_globals = {"__name__": "builtins"}
        mcp_shim(DummyBoxModel, mod_globals)
        self.assertIn("result", mod_globals)
        self.assertIsInstance(mod_globals["result"], Part)

    def test_non_builtins_hook_lazy(self):
        """When __name__ != 'builtins', result is NOT assigned."""
        mod_globals = {"__name__": "my_custom_module"}
        mcp_shim(DummyBoxModel, mod_globals)
        self.assertNotIn("result", mod_globals)


if __name__ == "__main__":
    unittest.main()
