"""Tests for build123d_forge.release packaging, checksums, and release notes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from build123d_forge.release import (
    compute_file_sha256,
    generate_release_notes,
    get_project_name,
    package_release,
)


def _populate_mock_dist(root: Path) -> Path:
    """Create a realistic dist/ tree with two variants, schemas/, and hidden metadata."""
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo-bracket"\nversion = "0.2.0"\n',
        encoding="utf-8",
    )
    dist = root / "dist"
    (dist / "schemas").mkdir(parents=True)
    (dist / "schemas" / "DemoBracket.schema.json").write_text("{}", encoding="utf-8")
    (dist / ".internal_cache").mkdir(parents=True)
    (dist / ".internal_cache" / "ignored.stl").write_bytes(b"ignore")

    for variant, wall_gap in [("default", 9.625), ("thick_glass", 12.8)]:
        var_dir = dist / variant
        var_dir.mkdir(parents=True)
        (var_dir / "demo_bracket.step").write_bytes(f"STEP-{variant}".encode("utf-8"))
        (var_dir / "demo_bracket.stl").write_bytes(f"STL-{variant}".encode("utf-8"))
        (var_dir / "demo_bracket_print.stl").write_bytes(f"PRINT-STL-{variant}".encode("utf-8"))
        (var_dir / "preview_grid.png").write_bytes(b"\x89PNG\r\n\x1a\nMOCK")
        manifest = {
            "schema_version": 1,
            "model_name": "DemoBracket",
            "variant_name": variant,
            "resolved_params": {"wall_gap": wall_gap, "thickness": 3.0},
            "metrics": {
                "watertight": True,
                "solids": 1,
                "faces": 18,
                "volume_mm3": 1450.25,
                "bounding_box_mm": [40.0, 25.0, 30.0],
            },
        }
        (var_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dist


def test_package_release_bundles_archives_flat_assets_and_checksums(tmp_path: Path) -> None:
    """package_release produces per-variant zips, flat <variant>__* assets, SHA256SUMS.txt, and RELEASE_NOTES.md."""
    dist = _populate_mock_dist(tmp_path)
    out_dir = tmp_path / "release_assets"

    result_path = package_release(dist_dir=dist, out_dir=out_dir, tag="v1.2.0")
    assert result_path == out_dir.resolve()

    # Per-variant zip archives exist and ignore schemas/ and hidden dirs
    assert (out_dir / "default.zip").is_file()
    assert (out_dir / "thick_glass.zip").is_file()
    assert not (out_dir / "schemas.zip").exists()

    with zipfile.ZipFile(out_dir / "default.zip", "r") as zf:
        names = set(zf.namelist())
        assert {
            "demo_bracket.step",
            "demo_bracket.stl",
            "demo_bracket_print.stl",
            "preview_grid.png",
            "manifest.json",
        }.issubset(names)

    # Flattened direct-download assets exist
    assert (out_dir / "default__demo_bracket_print.stl").is_file()
    assert (out_dir / "default__preview_grid.png").is_file()
    assert (out_dir / "thick_glass__demo_bracket.step").is_file()

    # SHA256SUMS.txt verifies every asset in standard sha256sum format
    checksum_lines = (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").strip().splitlines()
    assert len(checksum_lines) > 0
    recorded_files: set[str] = set()
    for line in checksum_lines:
        digest, filename = line.split("  ", maxsplit=1)
        recorded_files.add(filename)
        expected = hashlib.sha256((out_dir / filename).read_bytes()).hexdigest()
        assert digest == expected
        assert compute_file_sha256(out_dir / filename) == expected

    assert "default.zip" in recorded_files
    assert "default__demo_bracket_print.stl" in recorded_files
    assert "SHA256SUMS.txt" not in recorded_files
    assert "RELEASE_NOTES.md" not in recorded_files

    # Release notes include project name, quick downloads, metrics, and parameter table
    notes = (out_dir / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "# Release v1.2.0" in notes
    assert "**demo-bracket**" in notes
    assert "default__demo_bracket_print.stl" in notes
    assert "![default Preview Grid](default__preview_grid.png)" in notes
    assert "1450.25 mm³" in notes
    assert "`wall_gap`" in notes
    assert "`12.8`" in notes


def test_package_release_rejects_empty_tag(tmp_path: Path) -> None:
    """Empty or whitespace-only release tags raise ValueError."""
    dist = _populate_mock_dist(tmp_path)
    with pytest.raises(ValueError, match="Release tag cannot be empty"):
        package_release(dist_dir=dist, out_dir=tmp_path / "out", tag="   ")


def test_package_release_raises_on_missing_or_empty_dist(tmp_path: Path) -> None:
    """Missing dist directory or dist with only schemas/ raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="does not exist"):
        package_release(dist_dir=tmp_path / "missing", out_dir=tmp_path / "out", tag="v0.1.0")

    empty_dist = tmp_path / "empty_dist"
    (empty_dist / "schemas").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No built variant directories"):
        package_release(dist_dir=empty_dist, out_dir=tmp_path / "out", tag="v0.1.0")


def test_get_project_name_fallback_and_generate_release_notes_minimal(tmp_path: Path) -> None:
    """get_project_name falls back to directory name and generate_release_notes handles minimal assets."""
    assert get_project_name(tmp_path) == tmp_path.name
    notes = generate_release_notes(
        tag="v0.1.0",
        repo_name="minimal-part",
        variants=["base"],
        variant_manifests={"base": None},
        variant_assets={"base": ["base__part.stl", "base__iso.png"]},
        zip_names={"base": "base.zip"},
    )
    assert "![base Preview](base__iso.png)" in notes
    assert "[base__part.stl](base__part.stl)" in notes
