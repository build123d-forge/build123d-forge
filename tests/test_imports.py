"""Unit tests verifying package imports, versioning, and export contracts."""

import re
import sys
import unittest
from pathlib import Path


class TestPackageImports(unittest.TestCase):
    """Test suite verifying package structure, installation, and public exports."""

    def test_package_import(self):
        """Package imports cleanly without errors."""
        import build123d_forge
        self.assertIsNotNone(build123d_forge)

    def test_version_string(self):
        """Package version follows Semantic Versioning."""
        import build123d_forge
        version = getattr(build123d_forge, "__version__", None)
        self.assertIsNotNone(version)
        self.assertTrue(re.match(r"^\d+\.\d+\.\d+", version), f"Invalid version string: {version}")

    def test_all_exports_present(self):
        """All documented public symbols are exported in __all__."""
        import build123d_forge
        expected_exports = [
            "ModelPrototype",
            "Param",
            "ValidationReport",
            "ViewSpec",
            "mcp_shim",
            "generate_part_info",
            "generate_part_info_ast",
            "generate_part_info_code",
            "generate_json_schema",
            "serialize_json_schema",
            "export_json_schema",
            "export_schema",
        ]
        for symbol in expected_exports:
            self.assertIn(symbol, build123d_forge.__all__, f"{symbol} missing from __all__")
            self.assertTrue(hasattr(build123d_forge, symbol), f"{symbol} missing on module")

    def test_submodules_importable(self):
        """Individual submodules are importable."""
        import build123d_forge
        import build123d_forge.mcp_shim
        import build123d_forge.prototype as prototype_mod
        import build123d_forge.schema as schema_mod

        self.assertTrue(hasattr(prototype_mod, "ModelPrototype"))
        self.assertTrue(hasattr(schema_mod, "generate_json_schema"))
        mcp_shim_module = sys.modules["build123d_forge.mcp_shim"]
        self.assertTrue(hasattr(mcp_shim_module, "mcp_shim"))
        self.assertTrue(callable(build123d_forge.mcp_shim))

    def test_pth_source_resolution(self):
        """Package resolves to the expected src/ tree in build123d-forge."""
        import build123d_forge
        file_path = Path(build123d_forge.__file__).resolve()
        expected_path = Path("/home/tfoote/ws/onshape_mcp_test/build123d-forge/src/build123d_forge/__init__.py").resolve()
        self.assertEqual(file_path, expected_path)


if __name__ == "__main__":
    unittest.main()
