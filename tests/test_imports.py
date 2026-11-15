"""Tests verifying package imports, versioning, and public export contracts."""

from pathlib import Path
import re
import sys

import build123d_forge


def test_package_import() -> None:
    """Package imports cleanly without errors."""
    assert build123d_forge is not None


def test_version_string() -> None:
    """Package version follows Semantic Versioning."""
    version = getattr(build123d_forge, "__version__", None)
    assert version is not None
    assert re.match(r"^\d+\.\d+\.\d+", version), f"Invalid version string: {version}"


def test_all_exports_present() -> None:
    """All documented public symbols are exported in __all__ without duplicate aliases."""
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
        "ConfigError",
        "load_config_file",
        "read_config_file",
        "discover_config_files",
        "scaffold_config",
        "create_config_template",
        "BuildManifest",
        "clear_cache",
        "compute_ast_closure_hash",
        "compute_manifest",
        "compute_param_hash",
        "is_cache_valid",
        "build_project",
        "build_variant",
        "check_project",
        "list_project",
        "clean_dist",
        "package_release",
        "generate_release_notes",
        "watch_and_rebuild",
    ]
    for symbol in expected_exports:
        assert symbol in build123d_forge.__all__, f"{symbol} missing from __all__"
        assert hasattr(build123d_forge, symbol), f"{symbol} missing on module"

    removed_aliases = [
        "Manifest",
        "export_schema",
        "load_config",
        "watch_loop",
        "build_all_variants",
        "check_all",
        "list_variants",
    ]
    for alias in removed_aliases:
        assert alias not in build123d_forge.__all__


def test_submodules_importable() -> None:
    """Individual submodules are importable."""
    import build123d_forge.mcp_shim
    import build123d_forge.prototype as prototype_mod
    import build123d_forge.schema as schema_mod

    assert hasattr(prototype_mod, "ModelPrototype")
    assert hasattr(schema_mod, "generate_json_schema")
    mcp_shim_module = sys.modules["build123d_forge.mcp_shim"]
    assert hasattr(mcp_shim_module, "mcp_shim")
    assert callable(build123d_forge.mcp_shim)


def test_editable_source_resolution() -> None:
    """Editable package resolves to src/build123d_forge/__init__.py."""
    file_path = Path(build123d_forge.__file__).resolve()
    expected_path = (Path(__file__).resolve().parent.parent / "src" / "build123d_forge" / "__init__.py").resolve()
    assert file_path == expected_path
