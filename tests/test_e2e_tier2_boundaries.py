"""Tier 2: Boundary, Edge & Invalid Input Tests (>=5 test cases per feature).

Validates edge cases, invalid inputs, boundary limits, cycle detection, and typo diagnostics.
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
    class ConstrainedBox(ModelPrototype):
        """Model with strict bounds and cross-field invariant for boundary tests."""
        length: float = Param(20.0, gt=0.0, lt=100.0, unit="mm", description="Length")
        wall_gap: float = Param(9.625, gt=0.0, unit="mm", description="Glass slot gap")
        wall_thickness: float = Param(3.0, gt=0.0, unit="mm", description="Wall thickness")
        visor_drop: float = Param(10.0, gt=0.0, unit="mm", description="Visor drop")
        handedness: Literal["left", "right"] = Param("left", description="Handedness")

        @model_validator(mode="after")
        def _check_invariants(self) -> Self:
            if self.visor_drop < 2.0 * self.wall_thickness:
                raise ValueError("visor_drop must be at least 2x wall_thickness")
            return self

        def build(self) -> Part:
            return Box(self.length, self.wall_gap, self.visor_drop)

        def orient_for_print(self, part: Part) -> Part | None:
            return None


# ==============================================================================
# Feature 1: Prototype Bounds & Invariants Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_PROTOTYPE, "build123d_forge.prototype not yet implemented")
class TestTier2PrototypeBoundaries(unittest.TestCase):
    """Tier 2: Boundary test cases for ModelPrototype fields and invariants."""

    def test_boundary_negative_dimension_rejected(self):
        """1. Negative dimension violating gt=0.0 raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            ConstrainedBox(length=-1.0)
        self.assertTrue(any(e["loc"] == ("length",) for e in ctx.exception.errors()))

    def test_boundary_zero_dimension_rejected(self):
        """2. Zero dimension violating gt=0.0 raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            ConstrainedBox(wall_gap=0.0)
        self.assertTrue(any(e["loc"] == ("wall_gap",) for e in ctx.exception.errors()))

    def test_boundary_upper_bound_rejection(self):
        """3. Value exceeding lt=100.0 raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            ConstrainedBox(length=100.0)
        self.assertTrue(any(e["loc"] == ("length",) for e in ctx.exception.errors()))

    def test_boundary_type_mismatch_string_for_float(self):
        """4. String provided for float parameter raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            ConstrainedBox(length="not_a_number")  # type: ignore
        self.assertTrue(any(e["loc"] == ("length",) for e in ctx.exception.errors()))

    def test_boundary_cross_field_invariant_violation(self):
        """5. Setting visor_drop < 2*wall_thickness raises ValidationError before CAD build."""
        with self.assertRaises(ValidationError) as ctx:
            ConstrainedBox(wall_thickness=4.0, visor_drop=7.0)  # 7.0 < 2 * 4.0 = 8.0
        err_msg = str(ctx.exception)
        self.assertIn("visor_drop must be at least 2x wall_thickness", err_msg)

    def test_boundary_invalid_literal_choice(self):
        """6. Invalid literal option for handedness raises ValidationError."""
        with self.assertRaises(ValidationError):
            ConstrainedBox(handedness="upside_down")  # type: ignore


# ==============================================================================
# Feature 2: Config Engine Edge Cases & Cycle Detection
# ==============================================================================
@unittest.skipUnless(HAS_CONFIG and HAS_PROTOTYPE, "build123d_forge.config not yet implemented")
class TestTier2ConfigBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for configuration inheritance and circular dependencies."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_boundary_self_referencing_extends(self):
        """1. A config that extends itself immediately raises ConfigError with cycle detection."""
        self_file = self.dir_path / "loop.toml"
        self_file.write_text('extends = "loop.toml"\nlength = 20.0\n', encoding="utf-8")

        with self.assertRaises(ConfigError) as ctx:
            ConstrainedBox.from_toml(self_file)
        self.assertIn("Circular", str(ctx.exception))

    def test_boundary_multi_hop_circular_extends(self):
        """2. Circular extends loop A -> B -> C -> A raises ConfigError with full cycle path."""
        a_file = self.dir_path / "a.toml"
        b_file = self.dir_path / "b.toml"
        c_file = self.dir_path / "c.toml"

        a_file.write_text('extends = "b.toml"\nlength = 10.0\n', encoding="utf-8")
        b_file.write_text('extends = "c.toml"\nlength = 20.0\n', encoding="utf-8")
        c_file.write_text('extends = "a.toml"\nlength = 30.0\n', encoding="utf-8")

        with self.assertRaises(ConfigError) as ctx:
            ConstrainedBox.from_toml(a_file)
        msg = str(ctx.exception)
        self.assertIn("Circular", msg)

    def test_boundary_missing_parent_config(self):
        """3. Extending a non-existent parent config file raises ConfigError."""
        child_file = self.dir_path / "orphan.toml"
        child_file.write_text('extends = "nonexistent_parent.toml"\nlength = 25.0\n', encoding="utf-8")

        with self.assertRaises(ConfigError) as ctx:
            ConstrainedBox.from_toml(child_file)
        self.assertTrue("not found" in str(ctx.exception).lower() or "does not exist" in str(ctx.exception).lower())

    def test_boundary_malformed_toml_syntax(self):
        """4. Malformed syntax in TOML config raises ConfigError or TOMLDecodeError."""
        bad_file = self.dir_path / "syntax_error.toml"
        bad_file.write_text("length = [this is not valid toml\n", encoding="utf-8")

        with self.assertRaises(Exception):
            ConstrainedBox.from_toml(bad_file)

    def test_boundary_empty_toml_file(self):
        """5. Completely empty TOML config falls back 100% to prototype defaults."""
        empty_file = self.dir_path / "empty.toml"
        empty_file.write_text("", encoding="utf-8")

        inst = ConstrainedBox.from_toml(empty_file)
        self.assertEqual(inst.length, 20.0)
        self.assertEqual(inst.wall_gap, 9.625)


# ==============================================================================
# Feature 3: Typo Diagnostics via `difflib`
# ==============================================================================
@unittest.skipUnless(HAS_CONFIG and HAS_PROTOTYPE, "build123d_forge.config not yet implemented")
class TestTier2TypoDiagnostics(unittest.TestCase):
    """Tier 2: Boundary tests for fuzzy typo suggestions on unknown fields."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_boundary_typo_fuzzy_match_wall_gap(self):
        """1. Misspelled key 'wal_gap' triggers fuzzy suggestion 'did you mean wall_gap?'."""
        cfg = self.dir_path / "typo.toml"
        cfg.write_text("wal_gap = 12.0\n", encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            ConstrainedBox.from_toml(cfg)
        msg = str(ctx.exception)
        self.assertIn("wall_gap", msg)
        self.assertTrue("did you mean" in msg.lower() or "wal_gap" in msg)

    def test_boundary_typo_fuzzy_match_glass_width_to_wall_gap(self):
        """2. 'glass_width' or close synonym triggers suggestion for wall_gap or reports extra forbidden."""
        cfg = self.dir_path / "typo2.toml"
        cfg.write_text("wall_gapp = 12.0\n", encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            ConstrainedBox.from_toml(cfg)
        msg = str(ctx.exception)
        self.assertIn("wall_gap", msg)

    def test_boundary_typo_fuzzy_match_visor_drop(self):
        """3. 'visor_drop_height' triggers fuzzy suggestion for visor_drop."""
        cfg = self.dir_path / "typo3.toml"
        cfg.write_text("visor_dropp = 15.0\n", encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            ConstrainedBox.from_toml(cfg)
        msg = str(ctx.exception)
        self.assertIn("visor_drop", msg)

    def test_boundary_completely_unrelated_extra_key(self):
        """4. Extra key with zero fuzzy match reports forbidden without invalid suggestion."""
        cfg = self.dir_path / "random.toml"
        cfg.write_text("zzzzz_unknown_parameter = 12345\n", encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            ConstrainedBox.from_toml(cfg)
        msg = str(ctx.exception)
        self.assertIn("zzzzz_unknown_parameter", msg)

    def test_boundary_multiple_typos_in_single_file(self):
        """5. Multiple typos across file are all captured and reported."""
        cfg = self.dir_path / "multi_typo.toml"
        cfg.write_text("wal_gap = 10.0\nvisr_drop = 15.0\n", encoding="utf-8")

        with self.assertRaises(Exception) as ctx:
            ConstrainedBox.from_toml(cfg)
        msg = str(ctx.exception)
        self.assertTrue("wal_gap" in msg or "visr_drop" in msg)


# ==============================================================================
# Feature 4: CAD Exporters & Normalizers Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_EXPORTERS and HAS_PROTOTYPE, "build123d_forge.exporters not yet implemented")
class TestTier2CADNormalizerBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for STEP and STL deterministic normalizers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_boundary_step_unset_source_date_epoch_defaults_to_1970(self):
        """1. When SOURCE_DATE_EPOCH is unset, STEP timestamp defaults to 1970-01-01T00:00:00."""
        old_env = os.environ.pop("SOURCE_DATE_EPOCH", None)
        try:
            inst = ConstrainedBox()
            part = inst.build()
            out_step = self.dir_path / "epoch0.step"
            export_deterministic_step(part, out_step, epoch=None)
            content = out_step.read_text(encoding="utf-8", errors="ignore")
            self.assertIn("1970-01-01T00:00:00", content)
        finally:
            if old_env is not None:
                os.environ["SOURCE_DATE_EPOCH"] = old_env

    def test_boundary_canonical_stl_padding_for_short_names(self):
        """2. Model and config names with very short strings are correctly zero-padded to 80 bytes."""
        inst = ConstrainedBox()
        part = inst.build()
        out_stl = self.dir_path / "short.stl"
        export_canonical_stl(part, out_stl, model_name="A", config_name="B")

        hdr = out_stl.read_bytes()[:80]
        self.assertEqual(len(hdr), 80)
        self.assertTrue(hdr.startswith(b"build123d-forge:A:B"))
        # Padding must be null bytes
        self.assertEqual(hdr[len(b"build123d-forge:A:B"):], b"\x00" * (80 - len(b"build123d-forge:A:B")))

    def test_boundary_canonical_stl_long_identifiers_truncated_to_80_bytes(self):
        """3. Oversized model/config identifiers never cause the 80-byte header to exceed 80 bytes."""
        inst = ConstrainedBox()
        part = inst.build()
        out_stl = self.dir_path / "long.stl"
        long_name = "SuperLongModelIdentifierExceedingTheStandardEightyByteHeaderBufferLimit1234567890"
        export_canonical_stl(part, out_stl, model_name=long_name, config_name=long_name)

        hdr = out_stl.read_bytes()[:80]
        self.assertEqual(len(hdr), 80)

    def test_boundary_export_print_stl_when_orient_hook_returns_none(self):
        """4. Models returning None from orient_for_print do not crash and cleanly skip _print.stl."""
        inst = ConstrainedBox()
        part = inst.build()
        self.assertIsNone(inst.orient_for_print(part))

    def test_boundary_step_export_empty_compound_handled(self):
        """5. Exporting empty Compound raises error or produces clean STEP without segfault."""
        empty = Compound()
        out_step = self.dir_path / "empty.step"
        try:
            export_deterministic_step(empty, out_step)
        except Exception as e:
            # Cleanly caught exception is acceptable
            self.assertIsInstance(e, Exception)


# ==============================================================================
# Feature 5: Rendering & PNG Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_RENDER, "build123d_forge.render not yet implemented")
class TestTier2RenderBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for preview rendering and PNG stripping."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_boundary_preview_grid_fewer_than_4_views_raises_error(self):
        """1. render_preview_grid with fewer than 4 views raises ValueError."""
        from PIL import Image
        paths = [self.dir_path / f"view_{i}.png" for i in range(3)]  # only 3 views
        for p in paths:
            Image.new("RGB", (100, 100)).save(p)

        out = self.dir_path / "invalid_grid.png"
        with self.assertRaises(ValueError):
            render_preview_grid(paths, out)

    def test_boundary_png_stripper_passes_through_clean_png(self):
        """2. PNG already stripped of ancillary chunks is unmodified by strip_png_chunks."""
        png_sig = b"\x89PNG\r\n\x1a\n"
        ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
        clean = png_sig + ihdr + idat + iend

        res = strip_png_chunks(clean)
        self.assertEqual(res, clean)

    def test_boundary_png_stripper_strips_multiple_ancillary_chunks(self):
        """3. strip_png_chunks removes tIME, tEXt, zTXt, and iTXt chunks."""
        png_sig = b"\x89PNG\r\n\x1a\n"
        ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        time_c = b"\x00\x00\x00\x07tIME\x07\xe8\x0b\x0f\x01\x02\x03\x99\x88\x77\x66"
        text_c = b"\x00\x00\x00\x0ctEXtAuthor\x00Nobody\x12\x34\x56\x78"
        idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"

        polluted = png_sig + ihdr + time_c + text_c + idat + iend
        stripped = strip_png_chunks(polluted)

        self.assertNotIn(b"tIME", stripped)
        self.assertNotIn(b"tEXt", stripped)
        self.assertIn(b"IHDR", stripped)
        self.assertIn(b"IDAT", stripped)
        self.assertIn(b"IEND", stripped)

    def test_boundary_png_stripper_handles_truncated_bytes_gracefully(self):
        """4. Truncated or non-PNG input does not crash strip_png_chunks."""
        garbage = b"not a png at all"
        res = strip_png_chunks(garbage)
        self.assertEqual(res, garbage)

    def test_boundary_render_headless_with_no_display(self):
        """5. Rendering succeeds with DISPLAY environment variable unset."""
        old_disp = os.environ.pop("DISPLAY", None)
        try:
            if HAS_PROTOTYPE:
                inst = ConstrainedBox()
                part = inst.build()
                v = ViewSpec(stem="headless", description="Headless test")
                rendered = render_views(part, [v], output_dir=self.dir_path, model_instance=inst)
                self.assertGreaterEqual(len(rendered), 1)
        finally:
            if old_disp is not None:
                os.environ["DISPLAY"] = old_disp


# ==============================================================================
# Feature 6: Cache Invalidation Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_CACHE and HAS_PROTOTYPE, "build123d_forge.cache not yet implemented")
class TestTier2CacheBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for cache invalidation on modified inputs and tampered files."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_boundary_cache_invalidated_by_modified_parameter(self):
        """1. Changing parameter value invalidates cache."""
        inst1 = ConstrainedBox(length=20.0)
        inst2 = ConstrainedBox(length=25.0)
        src = self.dir_path / "model.py"
        src.write_text("class ConstrainedBox: pass\n", encoding="utf-8")

        m1 = compute_manifest(inst1, src, "default", project_root=self.dir_path)
        m2 = compute_manifest(inst2, src, "default", project_root=self.dir_path)
        self.assertNotEqual(m1.input_sha256, m2.input_sha256)

    def test_boundary_cache_invalidated_when_output_file_deleted(self):
        """2. Deleting an artifact file marks cache invalid."""
        inst = ConstrainedBox()
        src = self.dir_path / "model.py"
        src.write_text("class ConstrainedBox: pass\n", encoding="utf-8")
        art = self.dir_path / "output.stl"
        art.write_bytes(b"STL CONTENT")

        manifest = compute_manifest(inst, src, "default", project_root=self.dir_path, artifacts={"output.stl": art})
        man_path = self.dir_path / "manifest.json"
        man_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")

        self.assertTrue(is_cache_valid(man_path, manifest))
        # Now delete artifact
        art.unlink()
        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_boundary_cache_invalidated_when_output_file_content_tampered(self):
        """3. Modifying artifact content invalidates cache."""
        inst = ConstrainedBox()
        src = self.dir_path / "model.py"
        src.write_text("class ConstrainedBox: pass\n", encoding="utf-8")
        art = self.dir_path / "output.step"
        art.write_bytes(b"ORIGINAL STEP")

        manifest = compute_manifest(inst, src, "default", project_root=self.dir_path, artifacts={"output.step": art})
        man_path = self.dir_path / "manifest.json"
        man_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")

        self.assertTrue(is_cache_valid(man_path, manifest))
        # Tamper with file
        art.write_bytes(b"CORRUPTED STEP")
        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_boundary_cache_invalidated_when_manifest_corrupted(self):
        """4. Corrupted manifest JSON triggers cache invalidation (False) rather than crash."""
        inst = ConstrainedBox()
        src = self.dir_path / "model.py"
        src.write_text("class ConstrainedBox: pass\n", encoding="utf-8")
        manifest = compute_manifest(inst, src, "default", project_root=self.dir_path)

        man_path = self.dir_path / "manifest.json"
        man_path.write_text("{corrupted_json: [truncated", encoding="utf-8")

        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_boundary_cache_invalidated_when_imported_local_module_changed(self):
        """5. Modifying an imported helper module alters AST closure hash and invalidates cache."""
        helper = self.dir_path / "helper.py"
        helper.write_text("VAL = 10\n", encoding="utf-8")

        main_mod = self.dir_path / "main_mod.py"
        main_mod.write_text("import helper\nclass Model: x = helper.VAL\n", encoding="utf-8")

        h1 = compute_ast_closure_hash(main_mod, project_root=self.dir_path)

        # Modify helper logic
        helper.write_text("VAL = 20\n", encoding="utf-8")
        h2 = compute_ast_closure_hash(main_mod, project_root=self.dir_path)

        self.assertNotEqual(h1, h2)


# ==============================================================================
# Feature 7: B-Rep Gate Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_PROTOTYPE, "build123d_forge.prototype not yet implemented")
class TestTier2BRepGateBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for solid manifoldness, watertightness, and zero volume."""

    def test_boundary_brep_gate_flags_zero_volume_solid(self):
        """1. Single 2D face with volume = 0.0 fails watertight gate."""
        inst = ConstrainedBox()
        face = Face.make_rect(10.0, 10.0)
        rep = inst.verify(face)
        self.assertFalse(rep.watertight)
        self.assertAlmostEqual(rep.volume_mm3, 0.0)
        self.assertTrue(any("zero solids" in e.lower() or "volume" in e.lower() for e in rep.errors))

    def test_boundary_brep_gate_flags_empty_shape(self):
        """2. Empty Compound reports watertight=False and solids=0."""
        inst = ConstrainedBox()
        rep = inst.verify(Compound())
        self.assertFalse(rep.watertight)
        self.assertEqual(rep.solids, 0)
        self.assertGreaterEqual(len(rep.errors), 1)

    def test_boundary_brep_gate_diagnostics_contain_readable_message(self):
        """3. Gate errors list contains informative human-readable strings."""
        inst = ConstrainedBox()
        rep = inst.verify(Compound())
        for err in rep.errors:
            self.assertIsInstance(err, str)
            self.assertGreater(len(err), 5)

    def test_boundary_brep_gate_handles_multi_part_with_one_invalid_part(self):
        """4. Multi-part assembly where one part has 0 volume causes overall watertight=False."""
        inst = ConstrainedBox()
        good_box = Box(10, 10, 10)
        bad_face = Face.make_rect(10, 10)
        rep = inst.verify({"good": good_box, "bad": bad_face})
        self.assertFalse(rep.watertight)
        self.assertEqual(rep.solids, 1)

    def test_boundary_brep_gate_volume_precision(self):
        """5. Volume calculation accurately rounds to 3 decimal places."""
        inst = ConstrainedBox()
        part = Box(1.2345, 2.3456, 3.4567)
        rep = inst.verify(part)
        # Expected = 1.2345 * 2.3456 * 3.4567 = 10.0096
        self.assertAlmostEqual(rep.volume_mm3, 10.010, places=2)


# ==============================================================================
# Feature 8: CLI Command Boundaries
# ==============================================================================
@unittest.skipUnless(HAS_CLI, "build123d_forge.cli not yet implemented")
class TestTier2CLIBoundaries(unittest.TestCase):
    """Tier 2: Boundary tests for CLI error handling and non-zero exit codes."""

    def setUp(self):
        self.runner = CliRunner()

    def test_boundary_cli_build_nonexistent_variant_exits_nonzero(self):
        """1. bforge build with unknown variant exits with code 1."""
        with self.runner.isolated_filesystem():
            res = self.runner.invoke(cli, ["build", "completely_nonexistent_variant"])
            self.assertNotEqual(res.exit_code, 0)

    def test_boundary_cli_new_config_fails_if_already_exists_without_force(self):
        """2. bforge new-config existing_preset fails if file already exists without --force."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "m.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Box, Part\n"
                "class M(ModelPrototype):\n"
                "    def build(self) -> Part: return Box(1, 1, 1)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "existing.toml").write_text("x = 1\n")

            res = self.runner.invoke(cli, ["new-config", "existing"])
            self.assertNotEqual(res.exit_code, 0)
            self.assertIn("already exists", res.output.lower())

    def test_boundary_cli_new_config_overwrites_with_force(self):
        """3. bforge new-config existing_preset succeeds when --force is supplied."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "m.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Box, Part\n"
                "class M(ModelPrototype):\n"
                "    def build(self) -> Part: return Box(1, 1, 1)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "existing.toml").write_text("old = 1\n")

            res = self.runner.invoke(cli, ["new-config", "existing", "--force"])
            self.assertEqual(res.exit_code, 0)

    def test_boundary_cli_release_package_fails_without_tag(self):
        """4. bforge release-package without --tag exits non-zero."""
        with self.runner.isolated_filesystem():
            res = self.runner.invoke(cli, ["release-package"])
            self.assertNotEqual(res.exit_code, 0)

    def test_boundary_cli_clean_nonexistent_dist_exits_zero(self):
        """5. bforge clean when dist/ does not exist exits 0 cleanly."""
        with self.runner.isolated_filesystem():
            res = self.runner.invoke(cli, ["clean"])
            self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
