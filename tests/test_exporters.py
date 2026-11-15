"""Comprehensive unit tests for build123d-forge CAD exporters and determinism."""

import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from build123d import Axis, Box, Compound, Cylinder, Part

# Import directly from build123d_forge.exporters
from build123d_forge.exporters import (
    _resolve_epoch,
    export_canonical_stl,
    export_deterministic_step,
    export_print_stl,
    format_step_timestamp,
    make_canonical_stl_header,
    normalize_step_file,
)


class DummyOrientNoneModel:
    """Mock model whose orient_for_print returns None."""

    def build(self) -> Part:
        return Part(Box(10, 10, 10))

    def orient_for_print(self, part: Part | None = None) -> Part | None:
        return None


class DummyOrientPartModel:
    """Mock model whose orient_for_print returns an oriented Part."""

    def build(self) -> Part:
        return Part(Box(10, 20, 30))

    def orient_for_print(self, part: Part | None = None) -> Part:
        # Rotate 90 deg around X
        p = part if part is not None else self.build()
        return Part(p.rotate(axis=Axis.X, angle=90.0))


class DummyOrientMultipartModel:
    """Mock model whose orient_for_print returns a dictionary of parts."""

    def build(self) -> dict[str, Part]:
        b = Part(Box(10, 10, 10))
        l = Part(Box(10, 10, 2))
        return {"base": b, "lid": l}

    def orient_for_print(self, shape: Any = None) -> dict[str, Part]:
        b = Part(Box(10, 10, 10))
        l = Part(Box(10, 10, 2))
        return {"base": b, "lid": l}


class TestStepDeterminism(unittest.TestCase):
    """Tests for deterministic STEP export and timestamp normalization."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_epoch_resolution_explicit(self):
        """Explicit epoch argument takes precedence over environment variable."""
        old_env = os.environ.get("SOURCE_DATE_EPOCH")
        try:
            os.environ["SOURCE_DATE_EPOCH"] = "1000000000"
            self.assertEqual(_resolve_epoch(1700000000), 1700000000)
        finally:
            if old_env is not None:
                os.environ["SOURCE_DATE_EPOCH"] = old_env
            else:
                os.environ.pop("SOURCE_DATE_EPOCH", None)

    def test_epoch_resolution_from_env(self):
        """When epoch=None, SOURCE_DATE_EPOCH environment variable is parsed."""
        old_env = os.environ.get("SOURCE_DATE_EPOCH")
        try:
            os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
            self.assertEqual(_resolve_epoch(None), 1700000000)
        finally:
            if old_env is not None:
                os.environ["SOURCE_DATE_EPOCH"] = old_env
            else:
                os.environ.pop("SOURCE_DATE_EPOCH", None)

    def test_epoch_resolution_unset_defaults_to_zero(self):
        """When epoch is None and SOURCE_DATE_EPOCH is unset, defaults to 0."""
        old_env = os.environ.pop("SOURCE_DATE_EPOCH", None)
        try:
            self.assertEqual(_resolve_epoch(None), 0)
        finally:
            if old_env is not None:
                os.environ["SOURCE_DATE_EPOCH"] = old_env

    def test_epoch_resolution_invalid_env_falls_back(self):
        """Malformed SOURCE_DATE_EPOCH falls back cleanly to 0."""
        old_env = os.environ.get("SOURCE_DATE_EPOCH")
        try:
            os.environ["SOURCE_DATE_EPOCH"] = "not-a-number"
            self.assertEqual(_resolve_epoch(None), 0)
        finally:
            if old_env is not None:
                os.environ["SOURCE_DATE_EPOCH"] = old_env
            else:
                os.environ.pop("SOURCE_DATE_EPOCH", None)

    def test_format_step_timestamp_iso(self):
        """Unix epoch seconds formatted to standard ISO-8601 string."""
        self.assertEqual(format_step_timestamp(0), "1970-01-01T00:00:00")
        self.assertEqual(format_step_timestamp(1700000000), "2023-11-14T22:13:20")

    def test_step_timestamp_normalized_to_source_date_epoch(self):
        """STEP export contains exact ISO timestamp corresponding to SOURCE_DATE_EPOCH."""
        box = Box(20, 10, 5)
        out = self.dir_path / "model.step"
        export_deterministic_step(box, out, epoch=1700000000)
        content = out.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("2023-11-14T22:13:20", content)

    def test_step_export_bit_for_bit_identical(self):
        """Consecutive exports of the same shape with identical epoch produce identical SHA-256."""
        box = Box(25, 15, 8)
        out1 = self.dir_path / "m1.step"
        out2 = self.dir_path / "m2.step"

        export_deterministic_step(box, out1, epoch=1700000000)
        time.sleep(1.05)  # Ensure wall-clock advances
        export_deterministic_step(box, out2, epoch=1700000000)

        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(h1, h2)

    def test_step_export_empty_compound_raises_value_error(self):
        """Exporting an empty Compound raises ValueError with clear message."""
        empty = Compound()
        out = self.dir_path / "empty.step"
        with self.assertRaises(ValueError) as ctx:
            export_deterministic_step(empty, out)
        self.assertIn("empty Compound", str(ctx.exception))

    def test_step_export_multipart_compound(self):
        """Compound with multiple solids exports cleanly and deterministically."""
        b1 = Box(10, 10, 10)
        b2 = Box(10, 10, 10).translate((20, 0, 0))
        comp = Compound(children=[b1, b2])

        out1 = self.dir_path / "comp1.step"
        out2 = self.dir_path / "comp2.step"
        export_deterministic_step(comp, out1, epoch=1700000000)
        time.sleep(1.05)
        export_deterministic_step(comp, out2, epoch=1700000000)

        self.assertEqual(
            hashlib.sha256(out1.read_bytes()).hexdigest(),
            hashlib.sha256(out2.read_bytes()).hexdigest(),
        )

    def test_step_normalizer_replaces_multiline_filename(self):
        """normalize_step_file updates existing files with multi-line FILE_NAME."""
        test_step = self.dir_path / "manual.step"
        raw_text = (
            "ISO-10303-21;\nHEADER;\n"
            "FILE_NAME(\n  'custom_label',\n  '2026-11-15T12:00:00',\n  ('Author'),\n  ('Org'));\n"
            "ENDSEC;\nDATA;\nENDSEC;\n"
        )
        test_step.write_text(raw_text, encoding="utf-8")
        normalize_step_file(test_step, "1970-01-01T00:00:00")
        updated = test_step.read_text(encoding="utf-8")
        self.assertIn("'1970-01-01T00:00:00'", updated)
        self.assertNotIn("2026-11-15T12:00:00", updated)


class TestCanonicalStl(unittest.TestCase):
    """Tests for canonical binary STL export and locked deflection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_make_canonical_stl_header_short(self):
        """Header with short identifiers is padded with null bytes to 80 bytes."""
        hdr = make_canonical_stl_header("A", "B")
        self.assertEqual(len(hdr), 80)
        self.assertTrue(hdr.startswith(b"build123d-forge:A:B"))
        self.assertEqual(hdr[len(b"build123d-forge:A:B"):], b"\x00" * (80 - len(b"build123d-forge:A:B")))

    def test_make_canonical_stl_header_long_truncated(self):
        """Oversized identifiers are truncated to exactly 80 bytes without buffer overrun."""
        long_str = "X" * 100
        hdr = make_canonical_stl_header(long_str, long_str)
        self.assertEqual(len(hdr), 80)
        self.assertTrue(hdr.startswith(b"build123d-forge:XXXX"))

    def test_canonical_stl_writes_80_byte_header(self):
        """Canonical STL exporter formats 80-byte header as build123d-forge:<model>:<config>."""
        box = Box(20, 10, 5)
        out = self.dir_path / "model.stl"
        export_canonical_stl(box, out, model_name="SimpleBox", config_name="default")

        hdr = out.read_bytes()[:80]
        self.assertEqual(len(hdr), 80)
        self.assertTrue(hdr.startswith(b"build123d-forge:SimpleBox:default"))

    def test_canonical_stl_bit_for_bit_identical(self):
        """Consecutive binary STL exports produce identical SHA-256 checksums."""
        cyl = Cylinder(radius=10.0, height=20.0)
        out1 = self.dir_path / "c1.stl"
        out2 = self.dir_path / "c2.stl"

        export_canonical_stl(cyl, out1, model_name="CylinderModel", config_name="test")
        time.sleep(1.05)
        export_canonical_stl(cyl, out2, model_name="CylinderModel", config_name="test")

        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(h1, h2)

    def test_canonical_stl_file_size_structure(self):
        """Binary STL file matches 80-byte header + 4-byte uint32 facet count + 50 bytes per facet."""
        box = Box(20, 10, 5)
        out = self.dir_path / "box.stl"
        export_canonical_stl(box, out, model_name="Box", config_name="default")

        data = out.read_bytes()
        num_triangles = int.from_bytes(data[80:84], byteorder="little")
        expected_size = 84 + (50 * num_triangles)
        self.assertEqual(len(data), expected_size)

    def test_canonical_stl_deflections_alter_mesh_resolution(self):
        """Tighter linear and angular deflections increase facet count while keeping 80-byte header."""
        cyl = Cylinder(radius=10.0, height=20.0)
        coarse = self.dir_path / "coarse.stl"
        fine = self.dir_path / "fine.stl"

        export_canonical_stl(cyl, coarse, "Cyl", "coarse", linear_deflection=0.1, angular_deflection=0.2)
        export_canonical_stl(cyl, fine, "Cyl", "fine", linear_deflection=0.001, angular_deflection=0.01)

        self.assertEqual(coarse.read_bytes()[:80], b"build123d-forge:Cyl:coarse".ljust(80, b"\x00"))
        self.assertEqual(fine.read_bytes()[:80], b"build123d-forge:Cyl:fine".ljust(80, b"\x00"))
        self.assertGreater(fine.stat().st_size, coarse.stat().st_size)

    def test_canonical_stl_empty_compound_raises_value_error(self):
        """Exporting empty Compound to STL raises ValueError."""
        empty = Compound()
        out = self.dir_path / "empty.stl"
        with self.assertRaises(ValueError) as ctx:
            export_canonical_stl(empty, out)
        self.assertIn("empty Compound", str(ctx.exception))


class TestPrintOrientationExport(unittest.TestCase):
    """Tests for export_print_stl hook dispatch and multi-part support."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_export_print_stl_skips_when_orient_returns_none(self):
        """Returns None cleanly and produces no files when orient_for_print() returns None."""
        model = DummyOrientNoneModel()
        res = export_print_stl(model, self.dir_path, config_name="default")
        self.assertIsNone(res)
        self.assertEqual(len(list(self.dir_path.glob("*.stl"))), 0)

    def test_export_print_stl_single_part_to_directory(self):
        """Exports <variant>_print.stl when given output directory."""
        model = DummyOrientPartModel()
        res = export_print_stl(model, self.dir_path, config_name="custom")
        self.assertIsInstance(res, Path)
        self.assertTrue(res.is_file())
        self.assertEqual(res.name, "custom_print.stl")

        hdr = res.read_bytes()[:80]
        self.assertTrue(hdr.startswith(b"build123d-forge:DummyOrientPartModel:custom_print"))

    def test_export_print_stl_single_part_to_file_path(self):
        """Exports directly to specified file path when path ends with .stl."""
        model = DummyOrientPartModel()
        out_file = self.dir_path / "explicit_print.stl"
        res = export_print_stl(model, out_file, config_name="custom")
        self.assertEqual(res, out_file)
        self.assertTrue(out_file.is_file())

    def test_export_print_stl_multipart_assembly(self):
        """Exports each part in multi-part assembly to <variant>_<part>_print.stl."""
        model = DummyOrientMultipartModel()
        res = export_print_stl(model, self.dir_path, config_name="assembly")
        self.assertIsInstance(res, dict)
        self.assertIn("base", res)
        self.assertIn("lid", res)

        self.assertTrue(res["base"].is_file())
        self.assertTrue(res["lid"].is_file())
        self.assertEqual(res["base"].name, "assembly_base_print.stl")
        self.assertEqual(res["lid"].name, "assembly_lid_print.stl")

    def test_export_print_stl_prebuilt_shape_direct(self):
        """Passing a pre-oriented Part directly exports canonical STL."""
        part = Part(Box(10, 10, 10))
        res = export_print_stl(part, self.dir_path, model_name="DirectBox", config_name="v1")
        self.assertIsInstance(res, Path)
        self.assertTrue(res.is_file())
        self.assertEqual(res.name, "v1_print.stl")


if __name__ == "__main__":
    unittest.main()
