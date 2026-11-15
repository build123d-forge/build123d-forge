"""Unit tests for build123d_forge.cli Click command group and subcommands."""

import hashlib
import json
from pathlib import Path
import unittest
import zipfile

from click.testing import CliRunner

from build123d_forge.cli import cli, main


class TestBuild123dForgeCLI(unittest.TestCase):
    """Thorough unit test suite for bforge CLI commands."""

    def setUp(self):
        self.runner = CliRunner()

    def test_version_option(self):
        """bforge --version prints version and exits 0."""
        result = self.runner.invoke(cli, ["--version"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("version", result.output.lower())

    def test_help_option(self):
        """bforge --help lists all 8 subcommands and exits 0."""
        result = self.runner.invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        for cmd in ("build", "check", "new-config", "schema", "list", "clean", "watch", "release-package"):
            self.assertIn(cmd, result.output)

    def test_clean_existing_and_nonexistent(self):
        """bforge clean cleanly removes dist/ when present and exits 0 when absent."""
        with self.runner.isolated_filesystem():
            # When absent
            res = self.runner.invoke(cli, ["clean"])
            self.assertEqual(res.exit_code, 0)

            # When present
            dist = Path("dist")
            dist.mkdir()
            (dist / "dummy.txt").write_text("test")
            self.assertTrue(dist.exists())

            res = self.runner.invoke(cli, ["clean"])
            self.assertEqual(res.exit_code, 0)
            self.assertFalse(dist.exists())

    def test_new_config_and_force_overwrite(self):
        """bforge new-config creates template, refuses overwrite without --force, overwrites with --force."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "cube.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class Cube(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0, unit='mm', description='Side length')\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            # Create preset
            res = self.runner.invoke(cli, ["new-config", "cube_preset"])
            self.assertEqual(res.exit_code, 0)
            target = Path("configs") / "cube_preset.toml"
            self.assertTrue(target.is_file())
            self.assertIn("size = 10.0", target.read_text())

            # Attempt overwrite without force -> fails
            res_fail = self.runner.invoke(cli, ["new-config", "cube_preset"])
            self.assertNotEqual(res_fail.exit_code, 0)
            self.assertIn("already exists", res_fail.output.lower())

            # Overwrite with --force -> succeeds
            res_force = self.runner.invoke(cli, ["new-config", "cube_preset", "--force"])
            self.assertEqual(res_force.exit_code, 0)

    def test_schema_stdout_and_file_export(self):
        """bforge schema exports to stdout and writes dist/schemas/<Model>.schema.json."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "cube.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class Cube(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0, unit='mm')\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            # Schema to stdout
            res_stdout = self.runner.invoke(cli, ["schema", "--stdout"])
            self.assertEqual(res_stdout.exit_code, 0)
            self.assertIn("$schema", res_stdout.output)
            self.assertIn("Cube", res_stdout.output)

            # Schema to file
            res_file = self.runner.invoke(cli, ["schema"])
            self.assertEqual(res_file.exit_code, 0)
            schema_file = Path("dist") / "schemas" / "Cube.schema.json"
            self.assertTrue(schema_file.is_file())
            data = json.loads(schema_file.read_text())
            self.assertEqual(data["title"], "Cube")

    def test_list_empty_and_populated(self):
        """bforge list reports models, configs, and cache status."""
        with self.runner.isolated_filesystem():
            # Empty workspace
            res_empty = self.runner.invoke(cli, ["list"])
            self.assertEqual(res_empty.exit_code, 0)

            # Populated workspace
            Path("models").mkdir()
            (Path("models") / "cube.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class Cube(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0)\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "small.toml").write_text("size = 5.0\n")

            res_pop = self.runner.invoke(cli, ["list"])
            self.assertEqual(res_pop.exit_code, 0)
            self.assertIn("Cube", res_pop.output)
            self.assertIn("small", res_pop.output)

    def test_check_valid_and_invalid(self):
        """bforge check passes valid models and fails defective B-Rep geometry."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            Path("configs").mkdir()

            # Valid model
            (Path("models") / "good.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Box, Part\n"
                "class Good(ModelPrototype):\n"
                "    def build(self) -> Part: return Box(10, 10, 10)\n"
            )
            (Path("configs") / "good.toml").write_text("# empty\n")
            res_good = self.runner.invoke(cli, ["check"])
            self.assertEqual(res_good.exit_code, 0)

            # Invalid geometry (2D face with 0 volume)
            (Path("models") / "good.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Face, Part\n"
                "class Good(ModelPrototype):\n"
                "    def build(self) -> Part: return Face.make_rect(10, 10)\n"
            )
            res_bad = self.runner.invoke(cli, ["check"])
            self.assertNotEqual(res_bad.exit_code, 0)

    def test_build_caching_and_force(self):
        """bforge build compiles variants, caches manifests, and honors --force."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "cube.py").write_text(
                "from build123d_forge import ModelPrototype, Param\n"
                "from build123d import Box, Part\n"
                "class Cube(ModelPrototype):\n"
                "    size: float = Param(10.0, gt=0.0)\n"
                "    def build(self) -> Part: return Box(self.size, self.size, self.size)\n"
            )
            Path("configs").mkdir()
            (Path("configs") / "v1.toml").write_text("size = 8.0\n")

            # 1. Initial build
            res1 = self.runner.invoke(cli, ["build"])
            self.assertEqual(res1.exit_code, 0)
            self.assertIn("[built]", res1.output)
            m_path = Path("dist") / "v1" / "manifest.json"
            self.assertTrue(m_path.is_file())

            # 2. Cached build
            res2 = self.runner.invoke(cli, ["build"])
            self.assertEqual(res2.exit_code, 0)
            self.assertIn("[cached]", res2.output)

            # 3. Forced build
            res3 = self.runner.invoke(cli, ["build", "--force"])
            self.assertEqual(res3.exit_code, 0)
            self.assertIn("[built]", res3.output)

    def test_release_package_pipeline(self):
        """bforge release-package validates --tag requirement and creates assets, notes, and sums."""
        with self.runner.isolated_filesystem():
            # Fails without --tag
            res_no_tag = self.runner.invoke(cli, ["release-package"])
            self.assertNotEqual(res_no_tag.exit_code, 0)

            # Setup dummy build
            dist_var = Path("dist") / "v1"
            dist_var.mkdir(parents=True)
            (dist_var / "cube.step").write_bytes(b"STEP_DATA")
            (dist_var / "cube.stl").write_bytes(b"STL_DATA")
            (dist_var / "manifest.json").write_text('{"parameters": {"size": 10.0}}')

            res_pkg = self.runner.invoke(cli, ["release-package", "--tag", "v1.0.0", "--out", "release_out"])
            self.assertEqual(res_pkg.exit_code, 0)

            out_dir = Path("release_out")
            self.assertTrue((out_dir / "v1.zip").is_file())
            self.assertTrue((out_dir / "v1__cube.step").is_file())
            self.assertTrue((out_dir / "v1__cube.stl").is_file())
            self.assertTrue((out_dir / "SHA256SUMS.txt").is_file())
            self.assertTrue((out_dir / "RELEASE_NOTES.md").is_file())

            # Verify checksum integrity
            sums_lines = (out_dir / "SHA256SUMS.txt").read_text().strip().splitlines()
            for line in sums_lines:
                sha, fname = line.split()
                target_file = out_dir / fname
                self.assertTrue(target_file.is_file())
                self.assertEqual(sha, hashlib.sha256(target_file.read_bytes()).hexdigest())

    def test_watch_once_flag(self):
        """bforge watch --once runs single cycle and exits cleanly."""
        with self.runner.isolated_filesystem():
            Path("models").mkdir()
            (Path("models") / "cube.py").write_text(
                "from build123d_forge import ModelPrototype\n"
                "from build123d import Box, Part\n"
                "class Cube(ModelPrototype):\n"
                "    def build(self) -> Part: return Box(10, 10, 10)\n"
            )
            res = self.runner.invoke(cli, ["watch", "--once"])
            self.assertEqual(res.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
