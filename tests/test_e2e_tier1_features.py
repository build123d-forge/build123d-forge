"""Tier 1: Core Feature Isolation Tests (>=5 test cases per feature).

Validates each core feature in complete isolation against specifications in PROJECT.md.
"""

import ast
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Literal, Self

from build123d import Axis, Box, Compound, Part, Plane
from click.testing import CliRunner
import jsonschema
from pydantic import BaseModel, ValidationError, model_validator

# Link src directory
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# Progressive module imports
try:
    from build123d_forge.prototype import (
        ModelPrototype,
        Param,
        ValidationReport,
        ViewSpec,
    )
    HAS_PROTOTYPE = True
except ImportError:
    HAS_PROTOTYPE = False
    class ModelPrototype: pass  # type: ignore
    def Param(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    class ViewSpec: pass  # type: ignore
    class ValidationReport: pass  # type: ignore

try:
    from build123d_forge.schema import export_json_schema
    HAS_SCHEMA = True
except ImportError:
    HAS_SCHEMA = False
    def export_json_schema(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.mcp_shim import mcp_shim
    HAS_MCP_SHIM = True
except ImportError:
    HAS_MCP_SHIM = False
    def mcp_shim(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.config import ConfigError, load_config_file
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False
    class ConfigError(Exception): pass  # type: ignore
    def load_config_file(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.exporters import (
        export_canonical_stl,
        export_deterministic_step,
    )
    HAS_EXPORTERS = True
except ImportError:
    HAS_EXPORTERS = False
    def export_canonical_stl(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    def export_deterministic_step(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.render import render_preview_grid, render_views, strip_png_chunks
    HAS_RENDER = True
except ImportError:
    HAS_RENDER = False
    def render_preview_grid(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    def render_views(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    def strip_png_chunks(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.cache import (
        BuildManifest,
        compute_ast_closure_hash,
        compute_manifest,
        is_cache_valid,
    )
    HAS_CACHE = True
except ImportError:
    HAS_CACHE = False
    class BuildManifest: pass  # type: ignore
    def compute_ast_closure_hash(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    def compute_manifest(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore
    def is_cache_valid(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

try:
    from build123d_forge.cli import cli
    HAS_CLI = True
except ImportError:
    HAS_CLI = False
    cli = None  # type: ignore


# Helper test models (only instantiated if HAS_PROTOTYPE)
if HAS_PROTOTYPE:
    class SimpleBoxPrototype(ModelPrototype):
        """Simple rectangular box for feature tests."""
        length: float = Param(20.0, gt=0.0, unit="mm", description="Length in mm")
        width: float = Param(10.0, gt=0.0, unit="mm", description="Width in mm")
        height: float = Param(5.0, gt=0.0, unit="mm", description="Height in mm")
        material: str = Param("PLA", description="Material tag")

        def build(self) -> Part:
            box = Box(self.length, self.width, self.height)
            box.label = "simple_box"
            return box

        def orient_for_print(self, part: Part) -> Part | None:
            return part.rotate(Axis.X, 180).translate((0.0, 0.0, self.height))

        def views(self) -> list[ViewSpec]:
            return [
                ViewSpec(stem="iso", description="Isometric", direction="front", azimuth=35, elevation=25),
                ViewSpec(stem="top", description="Top view", direction="top", azimuth=0, elevation=90),
                ViewSpec(stem="side", description="Side view", direction="side", azimuth=90, elevation=0),
                ViewSpec(stem="bed", description="Print bed view", direction="iso", azimuth=35, elevation=30, use_print_oriented=True),
            ]

    class MultiPartAssemblyPrototype(ModelPrototype):
        """Two-part assembly prototype."""
        base_h: float = Param(10.0, gt=0.0, unit="mm", description="Base height")
        lid_h: float = Param(3.0, gt=0.0, unit="mm", description="Lid height")

        def build(self) -> dict[str, Part]:
            p1 = Box(20.0, 20.0, self.base_h)
            p1.label = "assembly_base"
            p2 = Box(20.0, 20.0, self.lid_h).translate((0.0, 0.0, self.base_h))
            p2.label = "assembly_lid"
            return {"base": p1, "lid": p2}


# ==============================================================================
# Feature 1: ModelPrototype & Param Foundation
# ==============================================================================
@unittest.skipUnless(HAS_PROTOTYPE, "build123d_forge.prototype not yet implemented")
class TestTier1ModelPrototype(unittest.TestCase):
    """Tier 1: Feature tests for ModelPrototype, Param, and ViewSpec in isolation."""

    def test_prototype_instantiation_with_defaults(self):
        """1. Prototype correctly instantiates with declared field defaults."""
        inst = SimpleBoxPrototype()
        self.assertEqual(inst.length, 20.0)
        self.assertEqual(inst.width, 10.0)
        self.assertEqual(inst.height, 5.0)
        self.assertEqual(inst.material, "PLA")

    def test_prototype_frozen_immutability(self):
        """2. ModelPrototype instance is frozen and rejects attribute reassignment."""
        inst = SimpleBoxPrototype()
        with self.assertRaises(ValidationError):
            inst.length = 30.0  # type: ignore

    def test_prototype_extra_forbid(self):
        """3. Extra undeclared fields are strictly forbidden during validation."""
        with self.assertRaises(ValidationError) as ctx:
            SimpleBoxPrototype(length=20.0, undeclared_extra_field=123)  # type: ignore
        errs = ctx.exception.errors()
        self.assertTrue(any(e["type"] == "extra_forbidden" for e in errs))

    def test_param_metadata_wrapper_stores_units(self):
        """4. Param wrapper preserves physical unit metadata in json_schema_extra."""
        field = SimpleBoxPrototype.model_fields["length"]
        self.assertIsNotNone(field.json_schema_extra)
        self.assertEqual(field.json_schema_extra.get("unit"), "mm")
        self.assertEqual(field.description, "Length in mm")
        self.assertEqual(field.gt, 0.0)

    def test_prototype_build_returns_valid_part(self):
        """5. build() returns a valid build123d Part solid."""
        inst = SimpleBoxPrototype(length=30.0, width=15.0, height=8.0)
        part = inst.build()
        self.assertIsInstance(part, Part)
        self.assertTrue(part.is_valid)
        self.assertTrue(part.is_manifold)
        self.assertAlmostEqual(part.volume, 30.0 * 15.0 * 8.0, places=2)

    def test_prototype_views_returns_viewspec_list(self):
        """6. views() returns list of ViewSpec instances with valid camera configurations."""
        inst = SimpleBoxPrototype()
        v_list = inst.views()
        self.assertEqual(len(v_list), 4)
        stems = [v.stem for v in v_list]
        self.assertIn("iso", stems)
        self.assertIn("bed", stems)
        self.assertTrue(v_list[3].use_print_oriented)


# ==============================================================================
# Feature 2: B-Rep Solid Gate & Geometric Verification
# ==============================================================================
@unittest.skipUnless(HAS_PROTOTYPE, "build123d_forge.prototype not yet implemented")
class TestTier1BRepGate(unittest.TestCase):
    """Tier 1: Feature tests for B-Rep solid verification gate."""

    def test_verify_gate_passes_on_watertight_solid(self):
        """1. verify() returns ValidationReport(watertight=True) on valid Part."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        rep = inst.verify(part)
        self.assertTrue(rep.is_valid)
        self.assertTrue(rep.is_manifold)
        self.assertTrue(rep.watertight)
        self.assertEqual(rep.solids, 1)
        self.assertAlmostEqual(rep.volume_mm3, 1000.0, places=2)
        self.assertEqual(len(rep.errors), 0)

    def test_verify_gate_bounding_box_metrics(self):
        """2. verify() computes exact bounding box dimensions (X, Y, Z)."""
        inst = SimpleBoxPrototype(length=40.0, width=20.0, height=10.0)
        part = inst.build()
        rep = inst.verify(part)
        self.assertEqual(rep.bounding_box_mm, (40.0, 20.0, 10.0))

    def test_verify_gate_face_count(self):
        """3. verify() reports correct topological face count for a rectangular prism."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        rep = inst.verify(part)
        self.assertEqual(rep.faces, 6)

    def test_verify_gate_flags_zero_solids(self):
        """4. verify() reports watertight=False and logs error when shape has zero solids."""
        inst = SimpleBoxPrototype()
        empty_compound = Compound()
        rep = inst.verify(empty_compound)
        self.assertFalse(rep.watertight)
        self.assertEqual(rep.solids, 0)
        self.assertTrue(any("zero solids" in e.lower() for e in rep.errors))

    def test_verify_gate_multi_part_dictionary(self):
        """5. verify() evaluates multi-part assemblies passed as dict[str, Part]."""
        inst = MultiPartAssemblyPrototype()
        parts_dict = inst.build()
        rep = inst.verify(parts_dict)
        self.assertTrue(rep.is_valid)
        self.assertTrue(rep.is_manifold)
        self.assertTrue(rep.watertight)
        self.assertEqual(rep.solids, 2)
        expected_vol = (20.0 * 20.0 * 10.0) + (20.0 * 20.0 * 3.0)
        self.assertAlmostEqual(rep.volume_mm3, expected_vol, places=2)


# ==============================================================================
# Feature 3: JSON Schema Export & TOML Templating
# ==============================================================================
@unittest.skipUnless(HAS_PROTOTYPE, "build123d_forge.prototype not yet implemented")
class TestTier1SchemaExport(unittest.TestCase):
    """Tier 1: Feature tests for JSON Schema export and TOML templating."""

    def test_schema_conforms_to_draft_2020_12(self):
        """1. model_json_schema() conforms strictly to JSON Schema Draft 2020-12."""
        schema = SimpleBoxPrototype.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        self.assertEqual(schema.get("type"), "object")

    def test_schema_preserves_field_descriptions_and_units(self):
        """2. Schema properties include descriptions and physical units."""
        schema = SimpleBoxPrototype.model_json_schema()
        props = schema["properties"]
        self.assertIn("length", props)
        self.assertEqual(props["length"].get("description"), "Length in mm")
        self.assertEqual(props["length"].get("unit"), "mm")

    def test_schema_forbids_additional_properties(self):
        """3. Exported schema enforces additionalProperties: false."""
        schema = SimpleBoxPrototype.model_json_schema()
        self.assertFalse(schema.get("additionalProperties", True))

    def test_schema_enforces_numeric_bounds(self):
        """4. Param bounds (gt=0.0) are mapped to schema exclusiveMinimum."""
        schema = SimpleBoxPrototype.model_json_schema()
        props = schema["properties"]
        self.assertEqual(props["length"].get("exclusiveMinimum"), 0.0)

    def test_to_toml_template_generates_documented_content(self):
        """5. to_toml_template() generates valid TOML containing schema ref, docs, and defaults."""
        template = SimpleBoxPrototype.to_toml_template(schema_url="../schemas/SimpleBox.schema.json")
        self.assertIn("#:schema ../schemas/SimpleBox.schema.json", template)
        self.assertIn("length = 20.0", template)
        self.assertIn("[mm]", template)
        self.assertIn("Length in mm", template)


# ==============================================================================
# Feature 4: build123d-mcp Compatibility Shim
# ==============================================================================
@unittest.skipUnless(HAS_MCP_SHIM and HAS_PROTOTYPE, "build123d_forge.mcp_shim not yet implemented")
class TestTier1McpShim(unittest.TestCase):
    """Tier 1: Feature tests for build123d-mcp compatibility shim."""

    def test_mcp_shim_populates_part_info(self):
        """1. mcp_shim synthesizes PART_INFO dictionary in module globals."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(SimpleBoxPrototype, mod_globals)
        self.assertIn("PART_INFO", mod_globals)
        info = mod_globals["PART_INFO"]
        self.assertIn("parameters", info)
        self.assertIn("length", info["parameters"])
        self.assertEqual(info["parameters"]["length"]["default"], 20.0)

    def test_mcp_shim_defines_make_callable(self):
        """2. mcp_shim defines make(**kwargs) callable returning Part."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(SimpleBoxPrototype, mod_globals)
        self.assertIn("make", mod_globals)
        self.assertTrue(callable(mod_globals["make"]))
        part = mod_globals["make"]()
        self.assertIsInstance(part, Part)
        self.assertAlmostEqual(part.volume, 1000.0, places=2)

    def test_mcp_shim_make_accepts_overrides(self):
        """3. make(**kwargs) applies parameter overrides."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(SimpleBoxPrototype, mod_globals)
        part = mod_globals["make"](length=50.0, height=10.0)
        self.assertAlmostEqual(part.volume, 50.0 * 10.0 * 10.0, places=2)

    def test_mcp_shim_defines_make_print_oriented(self):
        """4. mcp_shim defines make_print_oriented(**kwargs) returning oriented Part."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(SimpleBoxPrototype, mod_globals)
        self.assertIn("make_print_oriented", mod_globals)
        self.assertTrue(callable(mod_globals["make_print_oriented"]))
        oriented = mod_globals["make_print_oriented"]()
        self.assertIsInstance(oriented, Part)

    def test_mcp_shim_executes_builtins_result_hook(self):
        """5. In build123d-mcp execution environment (__name__ == 'builtins'), auto-assigns result."""
        mod_globals: dict[str, Any] = {"__name__": "builtins"}
        mcp_shim(SimpleBoxPrototype, mod_globals)
        self.assertIn("result", mod_globals)
        self.assertIsInstance(mod_globals["result"], Part)


# ==============================================================================
# Feature 5: Sparse TOML Parsing & Extends Inheritance
# ==============================================================================
@unittest.skipUnless(HAS_CONFIG and HAS_PROTOTYPE, "build123d_forge.config not yet implemented")
class TestTier1ConfigLoader(unittest.TestCase):
    """Tier 1: Feature tests for sparse TOML loading and extends resolution."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_sparse_toml_overrides_defaults(self):
        """1. Sparse TOML overriding only 1 parameter successfully merges with prototype defaults."""
        cfg_file = self.dir_path / "custom.toml"
        cfg_file.write_text("length = 45.0\n", encoding="utf-8")
        inst = SimpleBoxPrototype.from_toml(cfg_file)
        self.assertEqual(inst.length, 45.0)
        self.assertEqual(inst.width, 10.0)  # default preserved
        self.assertEqual(inst.height, 5.0)  # default preserved

    def test_load_config_single_level_extends(self):
        """2. extends directive loads and merges parent configuration parameters."""
        parent_file = self.dir_path / "base.toml"
        parent_file.write_text("length = 30.0\nwidth = 25.0\n", encoding="utf-8")

        child_file = self.dir_path / "child.toml"
        child_file.write_text('extends = "base.toml"\nheight = 12.0\n', encoding="utf-8")

        inst = SimpleBoxPrototype.from_toml(child_file)
        self.assertEqual(inst.length, 30.0)
        self.assertEqual(inst.width, 25.0)
        self.assertEqual(inst.height, 12.0)

    def test_load_config_child_overrides_parent(self):
        """3. Child parameters take precedence over extended parent parameters."""
        parent_file = self.dir_path / "base.toml"
        parent_file.write_text("length = 30.0\nwidth = 25.0\n", encoding="utf-8")

        child_file = self.dir_path / "child.toml"
        child_file.write_text('extends = "base.toml"\nlength = 99.0\n', encoding="utf-8")

        inst = SimpleBoxPrototype.from_toml(child_file)
        self.assertEqual(inst.length, 99.0)
        self.assertEqual(inst.width, 25.0)

    def test_load_config_multi_level_extends_chain(self):
        """4. Multi-level extends chain (C -> B -> A) correctly resolves in order."""
        a_file = self.dir_path / "a.toml"
        a_file.write_text("length = 10.0\nwidth = 10.0\nheight = 10.0\n", encoding="utf-8")

        b_file = self.dir_path / "b.toml"
        b_file.write_text('extends = "a.toml"\nwidth = 20.0\n', encoding="utf-8")

        c_file = self.dir_path / "c.toml"
        c_file.write_text('extends = "b.toml"\nheight = 30.0\n', encoding="utf-8")

        inst = SimpleBoxPrototype.from_toml(c_file)
        self.assertEqual(inst.length, 10.0)
        self.assertEqual(inst.width, 20.0)
        self.assertEqual(inst.height, 30.0)

    def test_load_config_strips_directives_from_model_data(self):
        """5. 'extends' directive is stripped before passing dictionary to Pydantic."""
        cfg_file = self.dir_path / "sub.toml"
        cfg_file.write_text('extends = "base.toml"\nlength = 15.0\n', encoding="utf-8")
        base_file = self.dir_path / "base.toml"
        base_file.write_text("width = 12.0\n", encoding="utf-8")

        # If extends wasn't stripped, Pydantic with extra='forbid' would raise ValidationError!
        inst = SimpleBoxPrototype.from_toml(cfg_file)
        self.assertEqual(inst.length, 15.0)


# ==============================================================================
# Feature 6: Deterministic CAD Exporters (STEP & STL)
# ==============================================================================
@unittest.skipUnless(HAS_EXPORTERS and HAS_PROTOTYPE, "build123d_forge.exporters not yet implemented")
class TestTier1DeterministicCAD(unittest.TestCase):
    """Tier 1: Feature tests for deterministic STEP and canonical STL exporters."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_step_timestamp_normalized_to_source_date_epoch(self):
        """1. STEP exporter writes ISO timestamp corresponding to SOURCE_DATE_EPOCH."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        out_step = self.dir_path / "model.step"
        # 1700000000 -> 2023-11-14T22:13:20
        export_deterministic_step(part, out_step, epoch=1700000000)
        content = out_step.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("2023-11-14T22:13:20", content)

    def test_step_export_bit_for_bit_identical(self):
        """2. Consecutive STEP exports with identical epoch produce identical SHA-256."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        out1 = self.dir_path / "m1.step"
        out2 = self.dir_path / "m2.step"
        export_deterministic_step(part, out1, epoch=1700000000)
        export_deterministic_step(part, out2, epoch=1700000000)

        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(h1, h2)

    def test_canonical_stl_writes_80_byte_header(self):
        """3. Canonical STL exporter formats 80-byte header as build123d-forge:<model>:<config>."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        out_stl = self.dir_path / "model.stl"
        export_canonical_stl(part, out_stl, model_name="SimpleBox", config_name="default")

        header = out_stl.read_bytes()[:80]
        expected_prefix = b"build123d-forge:SimpleBox:default"
        self.assertTrue(header.startswith(expected_prefix))
        self.assertEqual(len(header), 80)

    def test_canonical_stl_bit_for_bit_identical(self):
        """4. Consecutive binary STL exports produce identical SHA-256 checksums."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        out1 = self.dir_path / "m1.stl"
        out2 = self.dir_path / "m2.stl"
        export_canonical_stl(part, out1, model_name="SimpleBox", config_name="default")
        export_canonical_stl(part, out2, model_name="SimpleBox", config_name="default")

        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(h1, h2)

    def test_canonical_stl_file_size_structure(self):
        """5. Binary STL file matches standard size: 80 bytes header + 4 bytes uint32 + 50 bytes per facet."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        out_stl = self.dir_path / "box.stl"
        export_canonical_stl(part, out_stl, model_name="SimpleBox", config_name="default")

        data = out_stl.read_bytes()
        num_triangles = int.from_bytes(data[80:84], byteorder="little")
        expected_size = 84 + (50 * num_triangles)
        self.assertEqual(len(data), expected_size)


# ==============================================================================
# Feature 7: Preview Rendering & PNG Chunk Stripping
# ==============================================================================
@unittest.skipUnless(HAS_RENDER and HAS_PROTOTYPE, "build123d_forge.render not yet implemented")
class TestTier1PreviewRender(unittest.TestCase):
    """Tier 1: Feature tests for offscreen preview rendering and PNG normalization."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_render_views_produces_png_files(self):
        """1. render_views() produces preview PNG files for each defined ViewSpec."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        views = inst.views()
        rendered = render_views(part, views, output_dir=self.dir_path, model_instance=inst)
        self.assertGreaterEqual(len(rendered), 1)
        for p in rendered:
            self.assertTrue(p.is_file())
            self.assertTrue(p.name.endswith(".png"))

    def test_png_chunk_stripper_removes_time_and_text(self):
        """2. strip_png_chunks() removes non-essential metadata chunks (tIME, tEXt)."""
        # Minimal valid 1x1 PNG bytes
        png_sig = b"\x89PNG\r\n\x1a\n"
        ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        # Synthetic tIME chunk
        time_chunk = b"\x00\x00\x00\x07tIME\x07\xe8\x0b\x0f\x01\x02\x03\x99\x88\x77\x66"
        idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"

        raw_png = png_sig + ihdr + time_chunk + idat + iend
        stripped = strip_png_chunks(raw_png)

        self.assertNotIn(b"tIME", stripped)
        self.assertIn(b"IHDR", stripped)
        self.assertIn(b"IDAT", stripped)
        self.assertIn(b"IEND", stripped)

    def test_render_preview_grid_composites_4_views(self):
        """3. render_preview_grid() composites 4 view images into preview_grid.png."""
        from PIL import Image
        img_paths = []
        for i in range(4):
            p = self.dir_path / f"view_{i}.png"
            img = Image.new("RGBA", (1000, 750), color=(100 + i*30, 150, 200, 255))
            img.save(p)
            img_paths.append(p)

        grid_path = self.dir_path / "preview_grid.png"
        render_preview_grid(img_paths, grid_path)
        self.assertTrue(grid_path.is_file())

        with Image.open(grid_path) as im:
            self.assertEqual(im.size, (2000, 1500))

    def test_rendered_png_starts_with_png_signature(self):
        """4. Rendered PNG files start with standard 8-byte PNG signature."""
        inst = SimpleBoxPrototype()
        part = inst.build()
        v = ViewSpec(stem="test_view", description="Test", direction="front")
        rendered = render_views(part, [v], output_dir=self.dir_path, model_instance=inst)
        self.assertTrue(len(rendered) >= 1)
        data = rendered[0].read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_render_preview_grid_returns_output_path(self):
        """5. render_preview_grid returns Path pointing to created image."""
        from PIL import Image
        img_paths = [self.dir_path / f"v_{i}.png" for i in range(4)]
        for p in img_paths:
            Image.new("RGB", (10, 10)).save(p)
        out = self.dir_path / "grid_test.png"
        res = render_preview_grid(img_paths, out)
        self.assertEqual(res, out)
        self.assertTrue(out.is_file())


# ==============================================================================
# Feature 8: AST Closure Hashing & Content-Addressed Caching
# ==============================================================================
@unittest.skipUnless(HAS_CACHE and HAS_PROTOTYPE, "build123d_forge.cache not yet implemented")
class TestTier1CacheEngine(unittest.TestCase):
    """Tier 1: Feature tests for AST import closure hashing and manifest caching."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_ast_closure_hash_stable(self):
        """1. compute_ast_closure_hash returns reproducible hex digest for source file."""
        src_file = self.dir_path / "proto.py"
        src_file.write_text("class Foo: x = 1\n", encoding="utf-8")
        h1 = compute_ast_closure_hash(src_file, project_root=self.dir_path)
        h2 = compute_ast_closure_hash(src_file, project_root=self.dir_path)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_ast_hash_ignores_comments_and_whitespace(self):
        """2. Modifying comments and blank lines does NOT alter AST closure hash."""
        src_file = self.dir_path / "proto.py"
        src_file.write_text("class Foo:\n    x = 1\n", encoding="utf-8")
        h1 = compute_ast_closure_hash(src_file, project_root=self.dir_path)

        src_file.write_text("# Added comment\n\nclass Foo:\n    # Inner comment\n    x = 1\n\n\n", encoding="utf-8")
        h2 = compute_ast_closure_hash(src_file, project_root=self.dir_path)
        self.assertEqual(h1, h2)

    def test_compute_manifest_structure(self):
        """3. compute_manifest generates valid BuildManifest data model."""
        inst = SimpleBoxPrototype()
        src_file = self.dir_path / "box_proto.py"
        src_file.write_text("class BoxProto: pass\n", encoding="utf-8")

        manifest = compute_manifest(
            model_instance=inst,
            source_file=src_file,
            variant_name="default",
            project_root=self.dir_path,
        )
        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.variant_name, "default")
        self.assertIn("length", manifest.resolved_params)
        self.assertEqual(manifest.resolved_params["length"], 20.0)

    def test_is_cache_valid_returns_true_for_matching_manifest(self):
        """4. is_cache_valid returns True when input hash and artifact hashes match."""
        inst = SimpleBoxPrototype()
        src_file = self.dir_path / "box.py"
        src_file.write_text("class Box: pass\n", encoding="utf-8")

        # Fake artifact
        art_file = self.dir_path / "box.step"
        art_file.write_bytes(b"STEP DATA")

        manifest = compute_manifest(
            model_instance=inst,
            source_file=src_file,
            variant_name="default",
            project_root=self.dir_path,
            artifacts={"box.step": art_file},
        )
        manifest_path = self.dir_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")

        valid = is_cache_valid(manifest_path, manifest)
        self.assertTrue(valid)

    def test_is_cache_valid_sub_50ms(self):
        """5. Cache check executes in under 50ms without invoking CAD kernels."""
        inst = SimpleBoxPrototype()
        src_file = self.dir_path / "fast.py"
        src_file.write_text("class Fast: pass\n", encoding="utf-8")
        manifest = compute_manifest(
            model_instance=inst,
            source_file=src_file,
            variant_name="default",
            project_root=self.dir_path,
        )
        manifest_path = self.dir_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")

        start = time.perf_counter()
        valid = is_cache_valid(manifest_path, manifest)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertTrue(valid)
        self.assertLess(elapsed_ms, 50.0)


# ==============================================================================
# Feature 9: Command-Line Interface (`bforge`)
# ==============================================================================
@unittest.skipUnless(HAS_CLI, "build123d_forge.cli not yet implemented")
class TestTier1CommandLineInterface(unittest.TestCase):
    """Tier 1: Feature tests for bforge CLI commands in isolation."""

    def setUp(self):
        self.runner = CliRunner()

    def test_cli_version(self):
        """1. bforge --version outputs version and exits with 0."""
        result = self.runner.invoke(cli, ["--version"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("version", result.output.lower())

    def test_cli_help(self):
        """2. bforge --help lists subcommands and exits with 0."""
        result = self.runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("build", result.output)
        self.assertIn("check", result.output)
        self.assertIn("new-config", result.output)
        self.assertIn("schema", result.output)
        self.assertIn("clean", result.output)

    def test_cli_clean_command_removes_dist(self):
        """3. bforge clean removes dist/ directory cleanly."""
        with self.runner.isolated_filesystem():
            dist = Path("dist")
            dist.mkdir()
            (dist / "dummy.txt").write_text("junk")

            res = self.runner.invoke(cli, ["clean"])
            self.assertEqual(res.exit_code, 0)
            self.assertFalse(dist.exists())

    def test_cli_new_config_generates_file(self):
        """4. bforge new-config creates pre-populated .toml file."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "box.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class BoxModel(ModelPrototype):\n"
                "    wall: float = Param(3.0, gt=0.0, unit='mm', description='Wall thickness')\n"
                "    def build(self) -> Part: return Box(10, 10, self.wall)\n"
            )
            Path("configs").mkdir()
            res = self.runner.invoke(cli, ["new-config", "thick_wall"])
            self.assertEqual(res.exit_code, 0)
            cfg = Path("configs") / "thick_wall.toml"
            self.assertTrue(cfg.is_file())
            content = cfg.read_text()
            self.assertIn("wall = 3.0", content)

    def test_cli_list_command_succeeds(self):
        """5. bforge list runs and reports discovered models and configs."""
        with self.runner.isolated_filesystem():
            res = self.runner.invoke(cli, ["list"])
            self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
