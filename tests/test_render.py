"""Unit tests for headless VTK offscreen rendering, PNG chunk stripping, and preview grid compositor."""

import hashlib
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import build123d as bd
from build123d_forge.prototype import ModelPrototype, Param, ViewSpec
from build123d_forge.render import (
    DEFAULT_BG_COLOR,
    DEFAULT_MESH_COLOR,
    render_preview_grid,
    render_view,
    render_views,
    strip_png_chunks,
    strip_png_metadata,
)


class TestRenderBoxPrototype(ModelPrototype):
    """Test model prototype for rendering verification."""

    length: float = Param(25.0, gt=0.0, unit="mm")
    width: float = Param(15.0, gt=0.0, unit="mm")
    height: float = Param(8.0, gt=0.0, unit="mm")

    def build(self) -> bd.Part:
        box = bd.Box(self.length, self.width, self.height)
        box.label = "render_box"
        return box

    def orient_for_print(self, part: bd.Part | None = None) -> bd.Part:
        p = part if part is not None else self.build()
        return p.rotate(bd.Axis.X, 180).translate((0.0, 0.0, self.height))

    def views(self) -> list[ViewSpec]:
        return [
            ViewSpec(stem="iso", description="Isometric", direction="front", azimuth=35, elevation=25),
            ViewSpec(stem="top", description="Top", direction="top", azimuth=0, elevation=90),
            ViewSpec(stem="side", description="Side", direction="side", azimuth=90, elevation=0),
            ViewSpec(stem="bed", description="Print Bed", direction="iso", azimuth=35, elevation=30, use_print_oriented=True),
        ]


class TestPngMetadataStripper(unittest.TestCase):
    """Test suite verifying PNG chunk stripping and metadata determinism."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        # Standard minimal 1x1 RGBA PNG
        self.png_sig = b"\x89PNG\r\n\x1a\n"
        self.ihdr = b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        self.idat = b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        self.iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
        self.clean_png = self.png_sig + self.ihdr + self.idat + self.iend

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_strip_time_and_text_chunks(self):
        """strip_png_chunks removes tIME and tEXt chunks while keeping IHDR, IDAT, IEND."""
        time_chunk = b"\x00\x00\x00\x07tIME\x07\xe8\x0b\x0f\x01\x02\x03\x99\x88\x77\x66"
        text_chunk = b"\x00\x00\x00\x05tEXtAuthor\x00\x12\x34\x56\x78"
        polluted = self.png_sig + self.ihdr + time_chunk + text_chunk + self.idat + self.iend

        res = strip_png_chunks(polluted)
        self.assertNotIn(b"tIME", res)
        self.assertNotIn(b"tEXt", res)
        self.assertIn(b"IHDR", res)
        self.assertIn(b"IDAT", res)
        self.assertIn(b"IEND", res)

    def test_clean_png_unchanged(self):
        """Clean PNG without ancillary chunks remains bit-for-bit identical."""
        res = strip_png_chunks(self.clean_png)
        self.assertEqual(res, self.clean_png)

    def test_idempotent_stripping(self):
        """Repeated stripping is strictly idempotent."""
        once = strip_png_chunks(self.clean_png)
        twice = strip_png_chunks(once)
        self.assertEqual(once, twice)

    def test_corrupt_and_non_png_graceful(self):
        """Non-PNG or corrupted bytes return gracefully without raising exceptions."""
        self.assertEqual(strip_png_chunks(b""), b"")
        self.assertEqual(strip_png_chunks(b"GIF89a"), b"GIF89a")
        self.assertEqual(strip_png_chunks(b"\x89PNG\r\n\x1a\ncorrupt"), b"\x89PNG\r\n\x1a\ncorrupt")

    def test_strip_png_metadata_file_mode(self):
        """strip_png_metadata operates on files on disk (in-place and destination)."""
        src = self.dir_path / "test.png"
        src.write_bytes(self.clean_png)

        # In-place
        res1 = strip_png_metadata(src)
        self.assertEqual(res1, src)
        self.assertEqual(src.read_bytes(), self.clean_png)

        # Destination path
        dst = self.dir_path / "sub" / "stripped.png"
        res2 = strip_png_metadata(src, output_path=dst)
        self.assertEqual(res2, dst)
        self.assertTrue(dst.is_file())
        self.assertEqual(dst.read_bytes(), self.clean_png)

    def test_stripped_png_openable_by_pillow(self):
        """A real image saved by Pillow, then stripped, remains valid and openable by Pillow."""
        p = self.dir_path / "real.png"
        img = Image.new("RGBA", (50, 40), color=(120, 180, 240, 255))
        img.save(p)

        strip_png_metadata(p)
        with Image.open(p) as loaded:
            self.assertEqual(loaded.size, (50, 40))
            self.assertEqual(loaded.mode, "RGBA")


class TestOffscreenVtkRender(unittest.TestCase):
    """Test suite verifying headless offscreen VTK preview rendering."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_render_view_produces_valid_png_bytes(self):
        """render_view returns valid PNG bytes when output_path is None."""
        box = bd.Box(10, 10, 10)
        png_bytes = render_view(box, width=400, height=300)
        self.assertIsInstance(png_bytes, bytes)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

        with Image.open(io.BytesIO(png_bytes)) as im:
            self.assertEqual(im.size, (400, 300))

    def test_render_view_writes_to_file(self):
        """render_view writes to disk and returns Path when output_path is provided."""
        cyl = bd.Cylinder(radius=5, height=15)
        out = self.dir_path / "cylinder.png"
        res = render_view(cyl, output_path=out, width=500, height=400)
        self.assertEqual(res, out)
        self.assertTrue(out.is_file())

        with Image.open(out) as im:
            self.assertEqual(im.size, (500, 400))

    def test_render_background_color_matches_fafafa(self):
        """Corner pixels in rendered view match background color #FAFAFA."""
        box = bd.Box(5, 5, 5)
        png_bytes = render_view(box, width=200, height=200)
        with Image.open(io.BytesIO(png_bytes)) as im:
            # Top-left corner must be background
            pixel = im.getpixel((0, 0))
            self.assertEqual(pixel[:3], (250, 250, 250))

    def test_render_multi_part_assembly(self):
        """render_view cleanly handles multi-part assemblies (dict[str, Part])."""
        parts = {
            "base": bd.Box(20, 20, 5),
            "pillar": bd.Cylinder(radius=3, height=15).moved(bd.Location((0, 0, 10))),
        }
        out = self.dir_path / "assembly.png"
        res = render_view(parts, output_path=out, width=600, height=450)
        self.assertTrue(res.is_file())
        with Image.open(out) as im:
            self.assertEqual(im.size, (600, 450))

    def test_render_empty_shape_does_not_crash(self):
        """render_view on empty geometry returns a solid-color PNG rather than crashing."""
        out = self.dir_path / "empty.png"
        res = render_view(None, output_path=out, width=100, height=100)
        self.assertTrue(res.is_file())
        with Image.open(out) as im:
            self.assertEqual(im.size, (100, 100))
            self.assertEqual(im.getpixel((0, 0))[:3], (250, 250, 250))

    def test_render_views_suite_integration(self):
        """render_views renders all ViewSpec views including orient_for_print."""
        proto = TestRenderBoxPrototype()
        part = proto.build()
        views = proto.views()

        rendered = render_views(part, views, output_dir=self.dir_path, model_instance=proto)
        self.assertEqual(len(rendered), 4)

        expected_stems = {"iso", "top", "side", "bed"}
        actual_stems = {p.stem for p in rendered}
        self.assertEqual(expected_stems, actual_stems)

        for p in rendered:
            self.assertTrue(p.is_file())
            with Image.open(p) as im:
                self.assertEqual(im.size, (1000, 750))

    def test_headless_rendering_with_no_display(self):
        """Rendering succeeds without errors when DISPLAY environment variable is unset."""
        old_disp = os.environ.pop("DISPLAY", None)
        try:
            box = bd.Box(10, 10, 10)
            data = render_view(box, width=150, height=150)
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        finally:
            if old_disp is not None:
                os.environ["DISPLAY"] = old_disp


class TestPreviewGridCompositor(unittest.TestCase):
    """Test suite verifying 2x2 preview grid compositing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preview_grid_composites_4_views(self):
        """render_preview_grid stitches 4 images into 2x2 grid of expected size."""
        paths = []
        for i in range(4):
            p = self.dir_path / f"v_{i}.png"
            Image.new("RGBA", (400, 300), color=(50 + i * 40, 100, 150, 255)).save(p)
            paths.append(p)

        grid_out = self.dir_path / "preview_grid.png"
        res = render_preview_grid(paths, grid_out)
        self.assertEqual(res, grid_out)
        self.assertTrue(grid_out.is_file())

        with Image.open(grid_out) as im:
            self.assertEqual(im.size, (800, 600))

    def test_preview_grid_fewer_than_4_views_raises(self):
        """render_preview_grid with fewer than 4 views raises ValueError."""
        paths = [self.dir_path / f"v_{i}.png" for i in range(3)]
        for p in paths:
            Image.new("RGB", (100, 100)).save(p)

        out = self.dir_path / "invalid.png"
        with self.assertRaises(ValueError):
            render_preview_grid(paths, out)

    def test_preview_grid_missing_file_raises(self):
        """render_preview_grid raises FileNotFoundError when a file is missing."""
        paths = [self.dir_path / f"non_existent_{i}.png" for i in range(4)]
        out = self.dir_path / "missing.png"
        with self.assertRaises(FileNotFoundError):
            render_preview_grid(paths, out)

    def test_preview_grid_strips_metadata(self):
        """Output grid file has all ancillary metadata chunks stripped."""
        paths = []
        for i in range(4):
            p = self.dir_path / f"v_{i}.png"
            Image.new("RGBA", (100, 100), color=(100, 100, 100, 255)).save(p)
            paths.append(p)

        out = self.dir_path / "clean_grid.png"
        render_preview_grid(paths, out)
        data = out.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotIn(b"tIME", data)
        self.assertNotIn(b"tEXt", data)


class TestRenderDeterminism(unittest.TestCase):
    """Test suite verifying bit-for-bit reproducible SHA-256 hashes across renders."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_render_view_bit_for_bit_determinism(self):
        """Repeated renders of identical geometry and camera produce identical SHA-256."""
        proto = TestRenderBoxPrototype()
        part = proto.build()
        view = ViewSpec(stem="iso", description="Isometric", direction="front", azimuth=35, elevation=25)

        bytes1 = render_view(part, view=view, width=400, height=300)
        bytes2 = render_view(part, view=view, width=400, height=300)

        h1 = hashlib.sha256(bytes1).hexdigest()
        h2 = hashlib.sha256(bytes2).hexdigest()
        self.assertEqual(h1, h2, "Repeated offscreen renders must be bit-for-bit reproducible")

    def test_preview_grid_bit_for_bit_determinism(self):
        """Repeated grid compositing produces identical SHA-256."""
        views = []
        for i in range(4):
            p = self.dir_path / f"v_{i}.png"
            Image.new("RGBA", (200, 150), color=(40 + i * 30, 80, 120, 255)).save(p)
            views.append(p)

        out1 = self.dir_path / "grid1.png"
        out2 = self.dir_path / "grid2.png"

        render_preview_grid(views, out1)
        render_preview_grid(views, out2)

        h1 = hashlib.sha256(out1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(out2.read_bytes()).hexdigest()
        self.assertEqual(h1, h2, "Repeated grid renders must be bit-for-bit identical")


if __name__ == "__main__":
    unittest.main()
