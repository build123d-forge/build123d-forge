"""Tier 4: Real-World End-to-End Application Scenarios.

Validates 5 complete, multi-step production application lifecycles including
the reference consumer AlpstugaShowerMount.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Literal, Self

from build123d import (
    Axis,
    Box,
    Compound,
    Face,
    Part,
    Plane,
    Polygon,
    RectangleRounded,
    SlotOverall,
    extrude,
    mirror,
)
from click.testing import CliRunner
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


# Reference Consumer Model: AlpstugaShowerMount
if HAS_PROTOTYPE:
    class AlpstugaShowerMount(ModelPrototype):
        """Symmetric over-the-glass shower mount for the IKEA ALPSTUGA sensor."""

        wall_gap: float = Param(
            9.625,
            gt=0.0,
            unit="mm",
            description="Glass wall slot width and uniform 4-sided visor standoff (3/8 in + 0.1 mm)",
        )
        visor_drop: float = Param(
            10.0,
            gt=0.0,
            unit="mm",
            description="Vertical drop of the 4-sided visor rim and sensor mounts",
        )
        sensor_clearance: float = Param(
            0.6,
            ge=0.0,
            unit="mm",
            description="Per-side sliding clearance around the scanned ALPSTUGA profile",
        )
        wall_thickness: float = Param(
            3.0,
            gt=0.0,
            unit="mm",
            description="Structural wall thickness of roof, visor rim, shroud, and buttresses",
        )
        handedness: Literal["left", "right"] = Param(
            "left",
            description="Side of the glass wall when viewing the screen inside the shower",
        )

        @model_validator(mode="after")
        def _check_geometric_bounds(self) -> Self:
            if self.visor_drop < 2.0 * self.wall_thickness:
                raise ValueError("visor_drop must be at least 2x wall_thickness for zip-tie clearance")
            return self

        def build(self) -> Part:
            """Construct functional B-Rep Part following consumer geometric rules."""
            # Outer block
            len_x = 90.52 + 2 * (self.wall_thickness + self.sensor_clearance) + self.wall_gap
            len_y = 40.90 + 2 * (self.wall_thickness + self.sensor_clearance)
            h_total = 56.65 + self.wall_thickness

            # Main outer shroud block
            block = Box(len_x, len_y, h_total)
            # Glass slot cut
            slot_cut = Box(self.wall_gap, len_y * 1.5, self.visor_drop * 2)
            slot_cut = slot_cut.translate((-len_x / 2 + self.wall_gap / 2 + self.wall_thickness, 0, h_total / 2 - self.visor_drop))
            part = block - slot_cut

            if self.handedness == "right":
                part = mirror(part, about=Plane.XZ)

            part.label = f"alpstuga_shower_mount_{self.handedness}"
            return part

        def orient_for_print(self, part: Part) -> Part | None:
            """Invert onto coplanar flat roof at Z=0 for support-free 3D printing."""
            oriented = part.rotate(Axis.X, 180).translate((0.0, 0.0, self.wall_thickness))
            oriented.label = f"{part.label}_print_oriented"
            return oriented

        def views(self) -> list[ViewSpec]:
            """Standard 4-view preview suite."""
            return [
                ViewSpec(stem="iso_shower", description="Shower-side display & visor", direction="front", azimuth=35.0, elevation=25.0),
                ViewSpec(stem="iso_rear_clip", description="Rear vent & zip-tie holes", direction="front", azimuth=145.0, elevation=25.0),
                ViewSpec(stem="bottom_airflow", description="Open bottom & 45-deg lips", direction="top", azimuth=25.0, elevation=155.0),
                ViewSpec(stem="print_bed", description="Inverted zero-support print bed", direction="iso", azimuth=35.0, elevation=30.0, use_print_oriented=True),
            ]

    class ParametricEnclosure(ModelPrototype):
        """Two-part electronics enclosure assembly."""
        length: float = Param(80.0, gt=0.0, unit="mm")
        width: float = Param(50.0, gt=0.0, unit="mm")
        base_h: float = Param(25.0, gt=0.0, unit="mm")
        lid_h: float = Param(8.0, gt=0.0, unit="mm")
        wall: float = Param(2.0, gt=0.0, unit="mm")

        def build(self) -> dict[str, Part]:
            # Hollow base box
            outer_base = Box(self.length, self.width, self.base_h)
            inner_base = Box(self.length - 2*self.wall, self.width - 2*self.wall, self.base_h - self.wall)
            inner_base = inner_base.translate((0, 0, self.wall / 2))
            base_part = outer_base - inner_base
            base_part.label = "enclosure_base"

            # Lid box
            outer_lid = Box(self.length, self.width, self.lid_h)
            inner_lid = Box(self.length - 2*self.wall, self.width - 2*self.wall, self.lid_h - self.wall)
            inner_lid = inner_lid.translate((0, 0, -self.wall / 2))
            lid_part = outer_lid - inner_lid
            lid_part.label = "enclosure_lid"

            return {"base": base_part, "lid": lid_part}

        def orient_for_print(self, part: Part | dict[str, Part]) -> Part | dict[str, Part] | None:
            if isinstance(part, dict):
                return {
                    "base": part["base"],
                    "lid": part["lid"].rotate(Axis.X, 180),
                }
            return None


class TestTier4Scenarios(unittest.TestCase):
    """Tier 4: Production-level end-to-end application scenarios."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    # ==========================================================================
    # Scenario 1: Reference Consumer AlpstugaShowerMount Standard 3/8" Glass
    # ==========================================================================
    @unittest.skipUnless(
        HAS_PROTOTYPE and HAS_EXPORTERS and HAS_RENDER and HAS_CACHE,
        "Core modules for Scenario 1 not yet implemented",
    )
    def test_scenario1_alpstuga_shower_mount_standard_lifecycle(self):
        """Scenario 1: Complete production build, verification, export, and caching of AlpstugaShowerMount."""
        dist_dir = self.dir_path / "dist" / "default"
        dist_dir.mkdir(parents=True)

        # 1. Instantiate model with standard 3/8" glass defaults
        mount = AlpstugaShowerMount()
        self.assertEqual(mount.wall_gap, 9.625)
        self.assertEqual(mount.visor_drop, 10.0)
        self.assertEqual(mount.handedness, "left")

        # 2. Build functional B-Rep
        part = mount.build()
        self.assertIsInstance(part, Part)

        # 3. Solid verification gate
        report = mount.verify(part)
        self.assertTrue(report.is_valid, f"Validity failed: {report.errors}")
        self.assertTrue(report.is_manifold, f"Manifoldness failed: {report.errors}")
        self.assertTrue(report.watertight, f"Watertightness failed: {report.errors}")
        self.assertEqual(report.solids, 1)
        self.assertGreater(report.volume_mm3, 50000.0)

        # 4. Deterministic CAD exports
        step_out = dist_dir / "alpstuga_shower_mount.step"
        export_deterministic_step(part, step_out, epoch=1700000000)
        self.assertTrue(step_out.is_file())
        step_bytes = step_out.read_bytes()
        self.assertIn(b"2023-11-14T22:13:20", step_bytes)

        stl_out = dist_dir / "alpstuga_shower_mount.stl"
        export_canonical_stl(part, stl_out, model_name="AlpstugaShowerMount", config_name="default")
        self.assertTrue(stl_out.is_file())
        self.assertEqual(stl_out.read_bytes()[:80], b"build123d-forge:AlpstugaShowerMount:default".ljust(80, b"\x00"))

        # 5. Print orientation export
        part_print = mount.orient_for_print(part)
        self.assertIsNotNone(part_print)
        print_stl_out = dist_dir / "alpstuga_shower_mount_print.stl"
        export_canonical_stl(part_print, print_stl_out, model_name="AlpstugaShowerMount", config_name="default_print")
        self.assertTrue(print_stl_out.is_file())

        # 6. Render preview views & 2x2 grid
        views = mount.views()
        rendered_views = render_views(part, views, output_dir=dist_dir, model_instance=mount)
        self.assertEqual(len(rendered_views), 4)

        grid_out = dist_dir / "preview_grid.png"
        render_preview_grid(rendered_views, grid_out)
        self.assertTrue(grid_out.is_file())
        from PIL import Image
        with Image.open(grid_out) as im:
            self.assertEqual(im.size, (2000, 1500))

        # 7. Write manifest.json
        src_stub = self.dir_path / "alpstuga_mount.py"
        src_stub.write_text("# stub source\n", encoding="utf-8")
        artifacts = {
            "alpstuga_shower_mount.step": step_out,
            "alpstuga_shower_mount.stl": stl_out,
            "alpstuga_shower_mount_print.stl": print_stl_out,
            "preview_grid.png": grid_out,
        }
        manifest = compute_manifest(mount, src_stub, "default", project_root=self.dir_path, artifacts=artifacts)
        man_path = dist_dir / "manifest.json"
        man_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")

        # 8. Verify sub-50ms cache hit on rebuild
        start = time.perf_counter()
        valid = is_cache_valid(man_path, manifest)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertTrue(valid)
        self.assertLess(elapsed_ms, 50.0)

    # ==========================================================================
    # Scenario 2: AlpstugaShowerMount 1/2" Glass Sparse Inheritance Preset
    # ==========================================================================
    @unittest.skipUnless(HAS_PROTOTYPE and HAS_CONFIG, "Prototype or Config not implemented")
    def test_scenario2_half_inch_glass_sparse_inheritance(self):
        """Scenario 2: half_inch_glass.toml inherits from default.toml with sparse wall_gap override."""
        cfg_dir = self.dir_path / "configs"
        cfg_dir.mkdir()

        # default.toml
        default_file = cfg_dir / "default.toml"
        default_file.write_text("wall_gap = 9.625\n", encoding="utf-8")

        # half_inch_glass.toml: only overrides wall_gap to 12.8 mm
        half_inch_file = cfg_dir / "half_inch_glass.toml"
        half_inch_file.write_text('extends = "default.toml"\nwall_gap = 12.8\n', encoding="utf-8")

        # Load child config
        mount_half = AlpstugaShowerMount.from_toml(half_inch_file)
        self.assertEqual(mount_half.wall_gap, 12.8)
        self.assertEqual(mount_half.visor_drop, 10.0)  # preserved default
        self.assertEqual(mount_half.wall_thickness, 3.0)  # preserved default
        self.assertEqual(mount_half.handedness, "left")  # preserved default

        # Verify geometric expansion
        mount_def = AlpstugaShowerMount.from_toml(default_file)
        part_def = mount_def.build()
        part_half = mount_half.build()

        # Slot gap difference is 12.8 - 9.625 = 3.175 mm
        bb_def = part_def.bounding_box().size
        bb_half = part_half.bounding_box().size
        self.assertAlmostEqual(bb_half.X - bb_def.X, 3.175, places=2)

    # ==========================================================================
    # Scenario 3: AlpstugaShowerMount Right-Handed Mirror Variant
    # ==========================================================================
    @unittest.skipUnless(HAS_PROTOTYPE and HAS_CONFIG, "Prototype or Config not implemented")
    def test_scenario3_right_handed_mirror_variant(self):
        """Scenario 3: Handedness mirror across Plane.XZ preserves volume, manifoldness, and label."""
        cfg_dir = self.dir_path / "configs"
        cfg_dir.mkdir()

        base_file = cfg_dir / "base.toml"
        base_file.write_text("wall_gap = 12.8\n", encoding="utf-8")

        right_file = cfg_dir / "half_inch_right.toml"
        right_file.write_text('extends = "base.toml"\nhandedness = "right"\n', encoding="utf-8")

        mount_right = AlpstugaShowerMount.from_toml(right_file)
        self.assertEqual(mount_right.handedness, "right")
        self.assertEqual(mount_right.wall_gap, 12.8)

        mount_left = AlpstugaShowerMount(wall_gap=12.8, handedness="left")

        part_left = mount_left.build()
        part_right = mount_right.build()

        self.assertEqual(part_right.label, "alpstuga_shower_mount_right")
        self.assertTrue(part_right.is_manifold)
        self.assertTrue(part_right.is_valid)

        # Volumes must match exactly
        self.assertAlmostEqual(part_left.volume, part_right.volume, places=2)

    # ==========================================================================
    # Scenario 4: Multi-Component Parametric Enclosure Assembly
    # ==========================================================================
    @unittest.skipUnless(HAS_PROTOTYPE and HAS_EXPORTERS, "Prototype or Exporters not implemented")
    def test_scenario4_parametric_enclosure_assembly(self):
        """Scenario 4: Multi-component enclosure builds base and lid, exports distinct STLs and combined STEP."""
        dist_dir = self.dir_path / "dist" / "enclosure"
        dist_dir.mkdir(parents=True)

        enclosure = ParametricEnclosure(length=60.0, width=40.0, base_h=20.0, lid_h=6.0, wall=2.0)
        assembly = enclosure.build()
        self.assertIsInstance(assembly, dict)
        self.assertIn("base", assembly)
        self.assertIn("lid", assembly)

        # Verify solid gates on both components
        rep = enclosure.verify(assembly)
        self.assertTrue(rep.watertight)
        self.assertEqual(rep.solids, 2)

        # Export individual STLs
        base_stl = dist_dir / "enclosure_base.stl"
        lid_stl = dist_dir / "enclosure_lid.stl"
        export_canonical_stl(assembly["base"], base_stl, "Enclosure", "base")
        export_canonical_stl(assembly["lid"], lid_stl, "Enclosure", "lid")

        self.assertTrue(base_stl.is_file())
        self.assertTrue(lid_stl.is_file())

        # Export compound STEP
        combined = Compound(children=list(assembly.values()))
        step_out = dist_dir / "enclosure.step"
        export_deterministic_step(combined, step_out, epoch=1700000000)
        self.assertTrue(step_out.is_file())

    # ==========================================================================
    # Scenario 5: Complete Automated GitHub Release Packaging Pipeline
    # ==========================================================================
    @unittest.skipUnless(HAS_CLI, "CLI not implemented")
    def test_scenario5_github_release_packaging_pipeline(self):
        """Scenario 5: Complete automated release: validate -> build -> package -> verify SHA256SUMS and notes."""
        with self.runner.isolated_filesystem():
            # Create models
            Path("models").mkdir()
            (Path("models") / "box.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class BoxModel(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0, unit='mm')\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            # Create configs
            Path("configs").mkdir()
            (Path("configs") / "var1.toml").write_text("size = 12.0\n")
            (Path("configs") / "var2.toml").write_text("size = 18.0\n")

            # 1. Check gates
            res_check = self.runner.invoke(cli, ["check"])
            self.assertEqual(res_check.exit_code, 0)

            # 2. Build variants
            res_build = self.runner.invoke(cli, ["build"])
            self.assertEqual(res_build.exit_code, 0)

            # 3. Release package
            out_rel = Path("release_assets")
            res_pkg = self.runner.invoke(cli, ["release-package", "--tag", "v1.0.0", "--out", str(out_rel)])
            self.assertEqual(res_pkg.exit_code, 0)

            # 4. Verify release structure
            self.assertTrue(out_rel.is_dir())
            sums_file = out_rel / "SHA256SUMS.txt"
            notes_file = out_rel / "RELEASE_NOTES.md"
            self.assertTrue(sums_file.is_file())
            self.assertTrue(notes_file.is_file())

            # Verify checksum integrity
            sums_lines = sums_file.read_text().strip().splitlines()
            self.assertGreaterEqual(len(sums_lines), 2)
            for line in sums_lines:
                if not line.strip():
                    continue
                expected_sha, fname = line.split()
                target_file = out_rel / fname
                self.assertTrue(target_file.is_file(), f"Bundled file {fname} not found in {out_rel}")
                actual_sha = hashlib.sha256(target_file.read_bytes()).hexdigest()
                self.assertEqual(expected_sha, actual_sha, f"Checksum mismatch for {fname}")

            # Verify zip archives can be opened and are non-empty
            zip_files = list(out_rel.glob("*.zip"))
            self.assertGreaterEqual(len(zip_files), 2)
            for zf in zip_files:
                with zipfile.ZipFile(zf, "r") as z:
                    self.assertGreaterEqual(len(z.namelist()), 1)


if __name__ == "__main__":
    unittest.main()
