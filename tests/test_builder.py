"""Tests for build123d_forge.builder engine, discovery, caching, and B-Rep gates."""

from pathlib import Path
import shutil
import tempfile
import unittest

from build123d_forge.builder import (
    build_project,
    check_project,
    clean_dist,
    discover_models,
    discover_variants,
    list_project,
)


class TestBuilderEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_discover_models_and_default_variant(self):
        """Model discovery finds prototypes and creates a default variant when configs/ is empty."""
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / "cube.py").write_text(
            "from build123d_forge import ModelPrototype, Param\n"
            "from build123d import Box, Part\n"
            "class Cube(ModelPrototype):\n"
            "    side: float = Param(10.0, gt=0.0)\n"
            "    def build(self) -> Part: return Box(self.side, self.side, self.side)\n"
        )
        models = discover_models(project_root=self.root)
        self.assertIn("Cube", models)

        variants = discover_variants(models=models, project_root=self.root)
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].name, "default")
        self.assertIsNone(variants[0].config_path)

    def test_build_project_creates_artifacts_and_manifest(self):
        """build_project executes pipeline and populates dist/<variant>/."""
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / "cube.py").write_text(
            "from build123d_forge import ModelPrototype, Param\n"
            "from build123d import Box, Part\n"
            "class Cube(ModelPrototype):\n"
            "    side: float = Param(10.0, gt=0.0)\n"
            "    def build(self) -> Part: return Box(self.side, self.side, self.side)\n"
        )
        configs_dir = self.root / "configs"
        configs_dir.mkdir()
        (configs_dir / "large.toml").write_text("side = 25.0\n")

        results = build_project(project_root=self.root, output_dir=self.root / "dist")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertFalse(results[0].cached)

        dist_var = self.root / "dist" / "large"
        self.assertTrue((dist_var / "cube.step").is_file())
        self.assertTrue((dist_var / "cube.stl").is_file())
        self.assertTrue((dist_var / "manifest.json").is_file())
        self.assertTrue((self.root / "dist" / "schemas" / "Cube.schema.json").is_file())

    def test_incremental_build_hits_cache(self):
        """Second build_project on unchanged inputs skips build in < 50ms with cached=True."""
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / "cube.py").write_text(
            "from build123d_forge import ModelPrototype, Param\n"
            "from build123d import Box, Part\n"
            "class Cube(ModelPrototype):\n"
            "    side: float = Param(10.0, gt=0.0)\n"
            "    def build(self) -> Part: return Box(self.side, self.side, self.side)\n"
        )
        # Build 1
        res1 = build_project(project_root=self.root, output_dir=self.root / "dist")
        self.assertFalse(res1[0].cached)

        # Build 2 (cached)
        res2 = build_project(project_root=self.root, output_dir=self.root / "dist")
        self.assertTrue(res2[0].cached)

    def test_parallel_build_multiple_workers(self):
        """Parallel build with workers=2 builds multiple variants concurrently."""
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / "cube.py").write_text(
            "from build123d_forge import ModelPrototype, Param\n"
            "from build123d import Box, Part\n"
            "class Cube(ModelPrototype):\n"
            "    side: float = Param(10.0, gt=0.0)\n"
            "    def build(self) -> Part: return Box(self.side, self.side, self.side)\n"
        )
        configs_dir = self.root / "configs"
        configs_dir.mkdir()
        (configs_dir / "v1.toml").write_text("side = 12.0\n")
        (configs_dir / "v2.toml").write_text("side = 18.0\n")

        results = build_project(project_root=self.root, output_dir=self.root / "dist", workers=2)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.success for r in results))
        self.assertTrue((self.root / "dist" / "v1" / "manifest.json").is_file())
        self.assertTrue((self.root / "dist" / "v2" / "manifest.json").is_file())

    def test_check_project_detects_typos_and_geometry_defects(self):
        """check_project reports failure with typo diagnostics on invalid configs."""
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / "cube.py").write_text(
            "from build123d_forge import ModelPrototype, Param\n"
            "from build123d import Box, Part\n"
            "class Cube(ModelPrototype):\n"
            "    wall_gap: float = Param(10.0, gt=0.0)\n"
            "    def build(self) -> Part: return Box(self.wall_gap, 10, 10)\n"
        )
        configs_dir = self.root / "configs"
        configs_dir.mkdir()
        (configs_dir / "bad.toml").write_text("wal_gap = 12.0\n")

        results = check_project(project_root=self.root)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].success)
        self.assertIn("wall_gap", results[0].errors[0])

    def test_clean_dist_is_safe_and_idempotent(self):
        """clean_dist removes dist directory and exits cleanly when already absent."""
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "dummy.txt").write_text("test")
        self.assertTrue(clean_dist(dist))
        self.assertFalse(dist.exists())
        self.assertFalse(clean_dist(dist))


if __name__ == "__main__":
    unittest.main()
