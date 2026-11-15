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


class DummyMultiPartSubscriptModel(ModelPrototype):
    """Parametric two-part assembly testing print orientation subscript access."""

    size: float = Param(10.0, gt=0.0)

    def build(self) -> dict[str, Part]:
        return {"b1": Box(self.size, self.size, self.size), "b2": Box(self.size, self.size, 2.0)}

    def orient_for_print(self, parts: dict[str, Part] | None = None) -> dict[str, Part]:
        p = parts if parts is not None else self.build()
        return {"b1": p["b1"].rotate(Axis.Z, 90), "b2": p["b2"]}


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

    def test_make_print_oriented_multipart_subscript(self):
        """make_print_oriented() passes raw dict to orient_for_print, allowing subscripting."""
        mod_globals = {"__name__": "models.assembly"}
        mcp_shim(DummyMultiPartSubscriptModel, mod_globals)
        po = mod_globals["make_print_oriented"]()
        self.assertIsInstance(po, Compound)
        self.assertEqual(len(po.solids()), 2)

    def test_hook_eager_when_name_is_none(self):
        """When __name__ is None (build123d-mcp session.namespace), result is eagerly assigned."""
        mod_globals = {}
        mcp_shim(DummyBoxModel, mod_globals)
        self.assertIn("result", mod_globals)
        self.assertIsInstance(mod_globals["result"], Part)

    def test_hook_eager_when_show_in_globals(self):
        """When 'show' is in globals (build123d-mcp session), result is eagerly assigned."""
        mod_globals = {"show": lambda *a, **k: None}
        mcp_shim(DummyBoxModel, mod_globals)
        self.assertIn("result", mod_globals)
        self.assertIsInstance(mod_globals["result"], Part)

    def test_part_info_ast_required_parameters(self):
        """ast.literal_eval succeeds on models declaring required parameters."""
        class ReqModel(ModelPrototype):
            req_name: str = Param(..., description="Part Name")
            req_size: float = Param(..., gt=0.0)
            def build(self): pass

        code = generate_part_info_code(ReqModel)
        self.assertNotIn("PydanticUndefined", code)
        node = generate_part_info_ast(ReqModel)
        extracted = ast.literal_eval(node.value)
        self.assertIsNone(extracted["parameters"]["req_name"]["default"])
        self.assertIsNone(extracted["parameters"]["req_size"]["default"])
        self.assertEqual(extracted["parameters"]["req_size"]["gt"], 0.0)

    def test_part_info_ast_optional_and_pep604_unions(self):
        """ast.literal_eval succeeds and unwraps PEP 604 Union types."""
        class UnionModel(ModelPrototype):
            opt_tag: str | None = Param(None)
            num_choice: int | float = Param(10)
            choice: Literal["a", "b"] | None = Param(None)
            def build(self): pass

        node = generate_part_info_ast(UnionModel)
        extracted = ast.literal_eval(node.value)
        self.assertEqual(extracted["parameters"]["opt_tag"]["type"], "str")
        self.assertIsNone(extracted["parameters"]["opt_tag"]["default"])
        self.assertEqual(extracted["parameters"]["num_choice"]["type"], "Union[int, float]")
        self.assertEqual(extracted["parameters"]["choice"]["type"], "str")
        self.assertEqual(extracted["parameters"]["choice"]["choices"], ["a", "b"])

    def test_part_info_ast_container_factories(self):
        """ast.literal_eval evaluates list and dict default_factory parameters."""
        class ContainerModel(ModelPrototype):
            tags: list[str] = Param(default_factory=list)
            meta: dict[str, int] = Param(default_factory=dict)
            coords: list[float] = Param([1.0, 2.0])
            def build(self): pass

        node = generate_part_info_ast(ContainerModel)
        extracted = ast.literal_eval(node.value)
        self.assertEqual(extracted["parameters"]["tags"]["default"], [])
        self.assertEqual(extracted["parameters"]["meta"]["default"], {})
        self.assertEqual(extracted["parameters"]["coords"]["default"], [1.0, 2.0])

    def test_part_info_ast_non_modelprototype_base_model(self):
        """generate_part_info fallback safely handles standard Pydantic BaseModel."""
        class PlainModel(BaseModel):
            id_code: str
            count: int = 5

        node = generate_part_info_ast(PlainModel)
        extracted = ast.literal_eval(node.value)
        self.assertIsNone(extracted["parameters"]["id_code"]["default"])
        self.assertEqual(extracted["parameters"]["count"]["default"], 5)

    def test_build123d_mcp_extract_part_info_roundtrip(self):
        """Simulates build123d-mcp library._extract_part_info directly on generated code."""
        from build123d_mcp.tools.library import _extract_part_info

        class LibraryPart(ModelPrototype):
            """Library test component."""
            serial: str = Param(..., description="Serial number")
            diameter: float = Param(12.5, gt=0.0, unit="mm")
            def build(self): pass

        source = generate_part_info_code(LibraryPart)
        extracted = _extract_part_info(source)
        self.assertEqual(extracted["description"], "Library test component.")
        self.assertIn("serial", extracted["parameters"])
        self.assertIsNone(extracted["parameters"]["serial"]["default"])
        self.assertEqual(extracted["parameters"]["diameter"]["default"], 12.5)
        self.assertEqual(extracted["parameters"]["diameter"]["unit"], "mm")

    def test_hook_eager_required_fields_does_not_crash(self):
        """When loaded in session namespace, models with required fields do not crash."""
        class ReqBoxModel(ModelPrototype):
            length: float = Param(..., gt=0.0, description="Required length")
            width: float = Param(10.0, gt=0.0)

            def build(self) -> Part:
                return Box(self.length, self.width, 5.0)

        # Test with __name__ is None (build123d-mcp session namespace)
        mod_globals = {"__name__": None}
        mcp_shim(ReqBoxModel, mod_globals)
        self.assertIn("PART_INFO", mod_globals)
        self.assertIn("make", mod_globals)
        self.assertNotIn("result", mod_globals)

        # Calling make with required argument succeeds
        part = mod_globals["make"](length=20.0)
        self.assertIsInstance(part, Part)
        self.assertAlmostEqual(part.volume, 1000.0, places=1)

    def test_hook_eager_when_show_present_required_fields_does_not_crash(self):
        """When 'show' is in globals, models with required fields do not crash."""
        class ReqBoxModel(ModelPrototype):
            length: float = Param(..., gt=0.0)

            def build(self) -> Part:
                return Box(self.length, 10.0, 5.0)

        mod_globals = {"show": lambda *a, **k: None}
        mcp_shim(ReqBoxModel, mod_globals)
        self.assertIn("PART_INFO", mod_globals)
        self.assertIn("make", mod_globals)
        self.assertNotIn("result", mod_globals)

    def test_mcp_shim_dynamic_exec_with_future_annotations(self):
        """mcp_shim resolves stringified ForwardRefs when defined in dynamic exec namespace."""
        code = '''
from __future__ import annotations
from typing import Literal
from build123d_forge import ModelPrototype, Param, mcp_shim
from build123d import Box, Part

class DynamicModel(ModelPrototype):
    flavor: Literal["vanilla", "chocolate"] = Param("vanilla")
    def build(self) -> Part:
        return Box(10, 10, 10)

mcp_shim(DynamicModel, globals())
'''
        ns = {}
        exec(code, ns)
        self.assertIn("PART_INFO", ns)
        self.assertEqual(ns["PART_INFO"]["parameters"]["flavor"]["choices"], ["vanilla", "chocolate"])
        self.assertEqual(ns["PART_INFO"]["parameters"]["flavor"]["type"], "str")
        self.assertIn("result", ns)


if __name__ == "__main__":
    unittest.main()
