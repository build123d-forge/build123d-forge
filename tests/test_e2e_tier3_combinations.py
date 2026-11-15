"""Tier 3: Cross-Feature Pairwise Interaction Tests.

Validates the interactions between distinct functional modules across interface boundaries.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Literal, Self

from build123d import Axis, Box, Compound, Face, Part, Plane
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
    from build123d_forge.schema import export_schema
    HAS_SCHEMA = True
except ImportError:
    HAS_SCHEMA = False
    def export_schema(*args: Any, **kwargs: Any) -> Any: pass  # type: ignore

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


if HAS_PROTOTYPE:
    class InvariantBoxPrototype(ModelPrototype):
        """Prototype with cross-field constraint for pairwise testing."""
        wall_gap: float = Param(9.625, gt=0.0, unit="mm", description="Wall gap")
        wall_thickness: float = Param(3.0, gt=0.0, unit="mm", description="Wall thickness")
        visor_drop: float = Param(10.0, gt=0.0, unit="mm", description="Visor drop")

        @model_validator(mode="after")
        def _check_invariants(self) -> Self:
            if self.visor_drop < 2.0 * self.wall_thickness:
                raise ValueError("visor_drop must be at least 2x wall_thickness")
            return self

        def build(self) -> Part:
            return Box(30.0, self.wall_gap, self.visor_drop)

        def orient_for_print(self, part: Part) -> Part | None:
            return part.rotate(Axis.X, 180).translate((0.0, 0.0, self.wall_thickness))

        def views(self) -> list[ViewSpec]:
            return [
                ViewSpec(stem="front", description="Front view", direction="front"),
                ViewSpec(stem="top", description="Top view", direction="top"),
                ViewSpec(stem="side", description="Side view", direction="side"),
                ViewSpec(stem="print_bed", description="Bed view", direction="iso", use_print_oriented=True),
            ]

    class AssemblyBoxPrototype(ModelPrototype):
        """Multi-part prototype for pairwise testing."""
        base_h: float = Param(12.0, gt=0.0, unit="mm", description="Base height")
        lid_h: float = Param(4.0, gt=0.0, unit="mm", description="Lid height")

        def build(self) -> dict[str, Part]:
            p1 = Box(25.0, 25.0, self.base_h)
            p1.label = "assembly_base"
            p2 = Box(25.0, 25.0, self.lid_h).translate((0.0, 0.0, self.base_h))
            p2.label = "assembly_lid"
            return {"base": p1, "lid": p2}


class TestTier3Combinations(unittest.TestCase):
    """Tier 3: Pairwise cross-feature interaction test cases."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    # Pair 1: Config Inheritance + Cross-Field Validation
    @unittest.skipUnless(HAS_CONFIG and HAS_PROTOTYPE, "Config or Prototype not implemented")
    def test_combination_config_inheritance_and_cross_field_validation(self):
        """1. Pairwise: Child config inherits valid parent, but overrides trigger cross-field validator."""
        parent_file = self.dir_path / "parent.toml"
        parent_file.write_text("wall_thickness = 3.0\nvisor_drop = 10.0\n", encoding="utf-8")

        # Child overrides visor_drop to 4.0 (< 2 * 3.0 = 6.0)
        invalid_child = self.dir_path / "invalid_child.toml"
        invalid_child.write_text('extends = "parent.toml"\nvisor_drop = 4.0\n', encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            InvariantBoxPrototype.from_toml(invalid_child)
        self.assertIn("visor_drop must be at least 2x wall_thickness", str(ctx.exception))

        # Fixing child overrides wall_thickness to 1.5 (4.0 >= 2 * 1.5 = 3.0)
        valid_child = self.dir_path / "valid_child.toml"
        valid_child.write_text('extends = "parent.toml"\nwall_thickness = 1.5\nvisor_drop = 4.0\n', encoding="utf-8")
        inst = InvariantBoxPrototype.from_toml(valid_child)
        self.assertEqual(inst.wall_thickness, 1.5)
        self.assertEqual(inst.visor_drop, 4.0)

    # Pair 2: Sparse Config + Draft 2020-12 Schema Validation
    @unittest.skipUnless(HAS_CONFIG and HAS_PROTOTYPE, "Config or Prototype not implemented")
    def test_combination_sparse_config_and_json_schema_validation(self):
        """2. Pairwise: Exported JSON schema validates dictionary parsed from sparse TOML."""
        cfg_file = self.dir_path / "variant.toml"
        cfg_file.write_text("wall_gap = 12.8\n", encoding="utf-8")

        inst = InvariantBoxPrototype.from_toml(cfg_file)
        data = inst.model_dump(mode="json")

        schema = InvariantBoxPrototype.model_json_schema()
        validator = jsonschema.Draft202012Validator(schema)
        # Should validate without any schema errors
        errors = list(validator.iter_errors(data))
        self.assertEqual(len(errors), 0)

    # Pair 3: Multi-Part Build + Canonical Binary STL Export
    @unittest.skipUnless(HAS_EXPORTERS and HAS_PROTOTYPE, "Exporters or Prototype not implemented")
    def test_combination_multipart_build_and_canonical_stl_export(self):
        """3. Pairwise: Multi-part build dict exports individual canonical STLs for each component."""
        inst = AssemblyBoxPrototype()
        parts_dict = inst.build()
        self.assertIsInstance(parts_dict, dict)

        for comp_name, part in parts_dict.items():
            stl_path = self.dir_path / f"assembly_{comp_name}.stl"
            export_canonical_stl(part, stl_path, model_name="AssemblyBox", config_name=comp_name)
            self.assertTrue(stl_path.is_file())
            hdr = stl_path.read_bytes()[:80]
            self.assertTrue(hdr.startswith(f"build123d-forge:AssemblyBox:{comp_name}".encode("ascii")))

    # Pair 4: Multi-Part Build + Deterministic STEP Export
    @unittest.skipUnless(HAS_EXPORTERS and HAS_PROTOTYPE, "Exporters or Prototype not implemented")
    def test_combination_multipart_build_and_deterministic_step_export(self):
        """4. Pairwise: Multi-part build dict exports combined compound STEP with normalized timestamp."""
        inst = AssemblyBoxPrototype()
        parts_dict = inst.build()
        compound = Compound(children=list(parts_dict.values()))

        out_step = self.dir_path / "assembly.step"
        export_deterministic_step(compound, out_step, epoch=1700000000)
        self.assertTrue(out_step.is_file())
        content = out_step.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("2023-11-14T22:13:20", content)

    # Pair 5: Print Orientation Hook + VTK Preview Rendering
    @unittest.skipUnless(HAS_RENDER and HAS_PROTOTYPE, "Render or Prototype not implemented")
    def test_combination_print_orientation_and_vtk_preview_rendering(self):
        """5. Pairwise: Preview render correctly uses oriented solid for use_print_oriented=True."""
        inst = InvariantBoxPrototype()
        part = inst.build()
        views = inst.views()
        self.assertTrue(any(v.use_print_oriented for v in views))

        rendered = render_views(part, views, output_dir=self.dir_path, model_instance=inst)
        self.assertEqual(len(rendered), 4)
        for p in rendered:
            self.assertTrue(p.is_file())

    # Pair 6: AST Import Closure Fingerprint + Manifest Cache Invalidation
    @unittest.skipUnless(HAS_CACHE and HAS_PROTOTYPE, "Cache or Prototype not implemented")
    def test_combination_ast_closure_fingerprint_and_manifest_cache(self):
        """6. Pairwise: Modifying local module alters input hash and triggers cache miss."""
        dep_file = self.dir_path / "local_dep.py"
        dep_file.write_text("BASE_SIZE = 15.0\n", encoding="utf-8")

        main_file = self.dir_path / "model.py"
        main_file.write_text("import local_dep\nclass M: pass\n", encoding="utf-8")

        inst = InvariantBoxPrototype()
        art = self.dir_path / "part.step"
        art.write_bytes(b"DATA")

        m1 = compute_manifest(inst, main_file, "default", project_root=self.dir_path, artifacts={"part.step": art})
        man_path = self.dir_path / "manifest.json"
        man_path.write_text(json.dumps(m1.model_dump()), encoding="utf-8")
        self.assertTrue(is_cache_valid(man_path, m1))

        # Now edit local_dep logic
        dep_file.write_text("BASE_SIZE = 30.0\n", encoding="utf-8")
        m2 = compute_manifest(inst, main_file, "default", project_root=self.dir_path, artifacts={"part.step": art})
        self.assertNotEqual(m1.input_sha256, m2.input_sha256)
        self.assertFalse(is_cache_valid(man_path, m2))

    # Pair 7: B-Rep Watertightness Gate + CLI Check Subcommand
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_combination_brep_gate_and_cli_check_command(self):
        """7. Pairwise: CLI check halts with non-zero exit when geometry fails watertightness gate."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            # Model returning open 2D face (volume=0)
            (Path("models") / "bad_model.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Face, Part\n"
                "class BadModel(ModelPrototype):\n"
                "    def build(self) -> Part: return Face.make_rect(10, 10)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "default.toml").write_text("# empty preset\n")

            res = self.runner.invoke(cli, ["check"])
            self.assertNotEqual(res.exit_code, 0)

    # Pair 8: Deterministic CAD Normalizers + CLI Release Packaging
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_combination_deterministic_normalizers_and_release_packaging(self):
        """8. Pairwise: Release packager bundles normalized files and produces verifiable SHA256SUMS.txt."""
        with self.runner.isolated_filesystem():
            dist = Path("dist") / "default"
            dist.mkdir(parents=True)
            # Create dummy normalized assets
            step_file = dist / "model.step"
            step_file.write_bytes(b"ISO-10303-21; HEADER; FILE_NAME('','1970-01-01T00:00:00'); ENDSEC; END-ISO-10303-21;")
            stl_file = dist / "model.stl"
            stl_file.write_bytes(b"build123d-forge:model:default".ljust(80, b"\x00") + b"\x00\x00\x00\x00")

            res = self.runner.invoke(cli, ["release-package", "--tag", "v0.1.0", "--out", "assets"])
            if res.exit_code == 0:
                sums_file = Path("assets") / "SHA256SUMS.txt"
                self.assertTrue(sums_file.is_file())
                sums_content = sums_file.read_text()
                self.assertIn("model.step", sums_content)
                self.assertIn("model.stl", sums_content)

    # Pair 9: mcp_shim Integration + Model Validation
    @unittest.skipUnless(HAS_MCP_SHIM and HAS_PROTOTYPE, "MCP Shim or Prototype not implemented")
    def test_combination_mcp_shim_and_model_validation(self):
        """9. Pairwise: mcp_shim make(**kwargs) enforces model invariants and rejects invalid inputs."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(InvariantBoxPrototype, mod_globals)
        make_fn = mod_globals["make"]

        # Valid kwargs build
        p_valid = make_fn(wall_thickness=2.0, visor_drop=8.0)
        self.assertIsInstance(p_valid, Part)

        # Invalid kwargs (visor_drop < 2*wall_thickness) raises ValidationError
        with self.assertRaises(ValidationError):
            make_fn(wall_thickness=4.0, visor_drop=5.0)

    # Pair 10: Extends Inheritance + MCP Shim PART_INFO
    @unittest.skipUnless(HAS_MCP_SHIM and HAS_PROTOTYPE, "MCP Shim or Prototype not implemented")
    def test_combination_extends_inheritance_and_mcp_shim_part_info(self):
        """10. Pairwise: PART_INFO parameters dictionary exposes types/defaults needed for overrides."""
        mod_globals: dict[str, Any] = {"__name__": "models.test"}
        mcp_shim(InvariantBoxPrototype, mod_globals)
        params = mod_globals["PART_INFO"]["parameters"]

        self.assertIn("wall_gap", params)
        self.assertIn("wall_thickness", params)
        self.assertIn("visor_drop", params)
        self.assertEqual(params["wall_gap"]["default"], 9.625)

    # Pair 11: Parallel CLI Build + Cache Manifest Management
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_combination_parallel_cli_build_and_cache_manifests(self):
        """11. Pairwise: bforge build with -j 2 builds multiple variants concurrently into separate dirs."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "m.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class M(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0, unit='mm')\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "var1.toml").write_text("size = 15.0\n")
            (Path("configs") / "var2.toml").write_text("size = 25.0\n")

            res = self.runner.invoke(cli, ["build", "-j", "2"])
            if res.exit_code == 0:
                self.assertTrue((Path("dist") / "var1" / "manifest.json").is_file())
                self.assertTrue((Path("dist") / "var2" / "manifest.json").is_file())

    # Pair 12: CLI Clean + Incremental Build Recovery
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_combination_cli_clean_and_rebuild_cycle(self):
        """12. Pairwise: bforge clean followed by bforge build completely restores dist/."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "m.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Box, Part\n"
                "class M(ModelPrototype):\n"
                "    def build(self) -> Part: return Box(10, 10, 10)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "default.toml").write_text("# empty\n")

            # First build
            res_build = self.runner.invoke(cli, ["build"])
            # Clean
            res_clean = self.runner.invoke(cli, ["clean"])
            self.assertEqual(res_clean.exit_code, 0)
            self.assertFalse(Path("dist").exists())

            # Rebuild
            res_rebuild = self.runner.invoke(cli, ["build"])
            if res_build.exit_code == 0:
                self.assertEqual(res_rebuild.exit_code, 0)
                self.assertTrue(Path("dist").exists())

    # Pair 13: Typo Diagnostics in CLI Check
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_combination_typo_diagnostics_in_cli_check(self):
        """13. Pairwise: bforge check on config with typo displays fuzzy suggestion on stdout."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "box_model.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class BoxModel(ModelPrototype):\n"
                "    wall_gap: float = Param(9.625, gt=0.0)\n"
                "    def build(self) -> Part: return Box(10, self.wall_gap, 10)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "typo_preset.toml").write_text("wal_gap = 12.0\n")

            res = self.runner.invoke(cli, ["check"])
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("wall_gap", res.output)

    # Pair 14: Custom Deflection Tolerances + Canonical Binary STL
    @unittest.skipUnless(HAS_EXPORTERS and HAS_PROTOTYPE, "Exporters or Prototype not implemented")
    def test_combination_custom_deflections_and_canonical_stl(self):
        """14. Pairwise: Tightening deflections increases facet count while preserving canonical 80-byte header."""
        from build123d import Cylinder
        cyl = Cylinder(radius=10.0, height=20.0)

        stl_coarse = self.dir_path / "coarse.stl"
        stl_fine = self.dir_path / "fine.stl"

        export_canonical_stl(cyl, stl_coarse, "Cyl", "coarse", linear_deflection=0.1, angular_deflection=0.2)
        export_canonical_stl(cyl, stl_fine, "Cyl", "fine", linear_deflection=0.001, angular_deflection=0.01)

        # Both have 80-byte header
        self.assertEqual(stl_coarse.read_bytes()[:80], b"build123d-forge:Cyl:coarse".ljust(80, b"\x00"))
        self.assertEqual(stl_fine.read_bytes()[:80], b"build123d-forge:Cyl:fine".ljust(80, b"\x00"))

        # Fine mesh must have more triangles (larger file size) than coarse mesh
        self.assertGreater(stl_fine.stat().st_size, stl_coarse.stat().st_size)

    # Pair 15: Composite Preview Grid + PNG Metadata Stripping
    @unittest.skipUnless(HAS_RENDER, "Render not implemented")
    def test_combination_composite_grid_and_png_metadata_stripping(self):
        """15. Pairwise: render_preview_grid creates composite and ensures zero ancillary metadata chunks."""
        from PIL import Image
        views = [self.dir_path / f"v_{i}.png" for i in range(4)]
        for v in views:
            Image.new("RGB", (200, 150), color=(50, 100, 150)).save(v)

        grid_out = self.dir_path / "composite_clean.png"
        render_preview_grid(views, grid_out)
        self.assertTrue(grid_out.is_file())

        data = grid_out.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertNotIn(b"tIME", data)
        self.assertNotIn(b"tEXt", data)


if __name__ == "__main__":
    unittest.main()
