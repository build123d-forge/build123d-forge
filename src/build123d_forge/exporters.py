"""Deterministic CAD exporters for build123d models (STEP & STL).

Provides bit-for-bit reproducible STEP output via SOURCE_DATE_EPOCH header
normalization and assembly usage occurrence canonicalization, canonical 80-byte
binary STL header synthesis with locked mesh deflection tolerances, and
print-orientation export dispatch.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from build123d import (
    Compound,
    Part,
    Shape,
    Solid,
    Unit,
    export_step,
    export_stl,
)

if TYPE_CHECKING:
    from build123d_forge.prototype import ModelPrototype

# Regex matching STEP FILE_NAME record: Group 1 captures preamble + name; replaces timestamp.
# Handles multi-line records and escaped quotes ('') per ISO 10303-21.
_STEP_FILE_NAME_PATTERN = re.compile(
    r"(FILE_NAME\s*\(\s*'(?:[^']|'')*'\s*,\s*)'(?:[^']|'')*'",
    re.DOTALL,
)

# Regex matching STEP NEXT_ASSEMBLY_USAGE_OCCURRENCE records:
# Group 1 captures preamble up to the quote; renumbers occurrence IDs sequentially from 1.
_STEP_ASSEMBLY_USAGE_PATTERN = re.compile(
    r"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*)'[^']*'",
    re.DOTALL,
)


def _is_empty_shape(shape: Any) -> bool:
    """Check if shape has no underlying CAD geometry."""
    if shape is None:
        return True
    if getattr(shape, "_wrapped", None) is None:
        return True
    try:
        wrapped = shape.wrapped
        if wrapped is None or wrapped.IsNull():
            return True
    except (AssertionError, AttributeError):
        return True
    if isinstance(shape, Compound):
        if not shape.children and not shape.solids() and not shape.faces() and not shape.edges():
            return True
    return False


def _resolve_epoch(epoch: int | None = None) -> int:
    """Resolve target epoch timestamp in seconds from argument or SOURCE_DATE_EPOCH."""
    if epoch is not None:
        try:
            return int(epoch)
        except (ValueError, TypeError):
            return 0

    env_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if env_epoch is not None and env_epoch.strip():
        try:
            return int(env_epoch.strip())
        except (ValueError, TypeError):
            return 0

    return 0


def format_step_timestamp(epoch: int) -> str:
    """Format Unix epoch seconds as ISO-8601 UTC timestamp for STEP FILE_NAME."""
    dt = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def normalize_step_file(file_path: Path | str, timestamp_str: str) -> None:
    """Normalize timestamps and assembly usage occurrences in a written STEP file."""
    path = Path(file_path)
    if not path.is_file():
        return

    content = path.read_text(encoding="utf-8", errors="ignore")

    # 1. Normalize FILE_NAME timestamp
    normalized = _STEP_FILE_NAME_PATTERN.sub(rf"\g<1>'{timestamp_str}'", content)

    # 2. Canonicalize NEXT_ASSEMBLY_USAGE_OCCURRENCE IDs from 1..N
    # OpenCASCADE increments a global static occurrence counter across exports,
    # which causes non-determinism in multi-part assembly STEP outputs.
    state = [0]

    def _replace_occurrence(match: re.Match[str]) -> str:
        state[0] += 1
        return f"{match.group(1)}'{state[0]}'"

    normalized = _STEP_ASSEMBLY_USAGE_PATTERN.sub(_replace_occurrence, normalized)

    if normalized != content:
        path.write_text(normalized, encoding="utf-8")


def export_deterministic_step(
    shape: Part | Solid | Compound | Shape,
    output_path: Path | str,
    epoch: int | None = None,
    unit: Unit = Unit.MM,
) -> Path:
    """Export shape to STEP (ISO 10303-21) with bit-for-bit deterministic timestamps.

    Normalizes the FILE_NAME header timestamp to `epoch` or `SOURCE_DATE_EPOCH`
    (defaulting to 0 / 1970-01-01T00:00:00 UTC), and canonicalizes assembly
    usage occurrence identifiers. This guarantees identical models produce
    bit-for-bit identical SHA-256 hashes across builds, processes, and machines.

    Args:
        shape: build123d Shape, Part, Solid, or Compound to export.
        output_path: Target .step output file path.
        epoch: Optional Unix epoch timestamp in seconds. If None, reads
            SOURCE_DATE_EPOCH environment variable, falling back to 0.
        unit: Unit system for geometric data (default: Unit.MM).

    Returns:
        Path to the written deterministic STEP file.

    Raises:
        ValueError: If shape is an empty Compound or has zero geometry.
    """
    if _is_empty_shape(shape):
        raise ValueError("Cannot export empty Compound or shape with no geometry to STEP")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    target_epoch = _resolve_epoch(epoch)
    iso_timestamp = format_step_timestamp(target_epoch)

    # build123d export_step accepts timestamp kwarg to set header directly
    export_step(shape, out, unit=unit, timestamp=iso_timestamp)

    # Defense-in-depth: normalize header timestamp and assembly occurrence IDs
    normalize_step_file(out, iso_timestamp)

    return out


def make_canonical_stl_header(model_name: str, config_name: str) -> bytes:
    """Generate canonical 80-byte binary STL header padded with null bytes.

    Format: `build123d-forge:<model_name>:<config_name>` padded to 80 bytes.
    If the string exceeds 80 bytes, it is cleanly truncated to 80 bytes.
    """
    ident = f"build123d-forge:{model_name}:{config_name}".encode("utf-8")[:80]
    return ident.ljust(80, b"\x00")


def export_canonical_stl(
    shape: Part | Solid | Compound | Shape,
    output_path: Path | str,
    model_name: str = "model",
    config_name: str = "default",
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.05,
    ascii_format: bool = False,
) -> Path:
    """Export shape to canonical binary STL with locked deflection and standard header.

    Replaces OpenCASCADE's variable 80-byte header with a deterministic
    `build123d-forge:<model>:<config>` header padded to 80 bytes with null bytes.
    Uses locked default mesh deflection tolerances (linear=0.01mm, angular=0.05rad)
    to guarantee identical facet generation and reproducible SHA-256 hashes.

    Args:
        shape: build123d Shape, Part, Solid, or Compound to export.
        output_path: Target .stl output file path.
        model_name: Model identifier for canonical header.
        config_name: Configuration preset identifier for canonical header.
        linear_deflection: Maximum chordal deviation in mm (default: 0.01).
        angular_deflection: Maximum angular deviation in radians (default: 0.05).
        ascii_format: Export ASCII STL instead of binary (default: False).

    Returns:
        Path to the written canonical STL file.

    Raises:
        ValueError: If shape is an empty Compound or has zero geometry.
    """
    if _is_empty_shape(shape):
        raise ValueError("Cannot export empty Compound or shape with no geometry to STL")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    export_stl(
        shape,
        out,
        tolerance=linear_deflection,
        angular_tolerance=angular_deflection,
        ascii_format=ascii_format,
    )

    if not ascii_format and out.is_file():
        header_bytes = make_canonical_stl_header(model_name, config_name)
        # In-place overwrite of exactly the first 80 bytes
        with open(out, "r+b") as f:
            f.seek(0)
            f.write(header_bytes)

    return out


def export_print_stl(
    model_or_shape: Any,
    output_path_or_dir: Path | str,
    model_name: str | None = None,
    config_name: str = "default",
    built_shape: Any = None,
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.05,
) -> Path | dict[str, Path] | None:
    """Export print-oriented STL(s) if print orientation is defined.

    Checks if `model_or_shape` defines `orient_for_print()` (or is an oriented
    shape itself). If `orient_for_print()` returns None, cleanly skips export
    and returns None. If a shape or dictionary of shapes is returned, exports
    canonical STL(s) using `<variant>_print.stl` naming convention.

    Args:
        model_or_shape: ModelPrototype instance, or Part/Solid/Compound/dict.
        output_path_or_dir: Target output file path or directory.
        model_name: Optional model identifier for canonical header.
        config_name: Variant/configuration name (default: "default").
        built_shape: Optional pre-built shape to pass to orient_for_print(shape).
        linear_deflection: Linear deflection tolerance (default: 0.01).
        angular_deflection: Angular deflection tolerance (default: 0.05).

    Returns:
        Path to output STL file, dict of Paths for multi-part, or None if skipped.
    """
    oriented: Any = None
    m_name = model_name

    # Check if model_or_shape provides orient_for_print hook
    if hasattr(model_or_shape, "orient_for_print") and callable(model_or_shape.orient_for_print):
        if m_name is None:
            m_name = getattr(model_or_shape, "__class__", type(model_or_shape)).__name__

        try:
            if built_shape is not None:
                try:
                    oriented = model_or_shape.orient_for_print(built_shape)
                except TypeError:
                    oriented = model_or_shape.orient_for_print()
            else:
                try:
                    oriented = model_or_shape.orient_for_print()
                except TypeError:
                    if hasattr(model_or_shape, "build") and callable(model_or_shape.build):
                        b = model_or_shape.build()
                        oriented = model_or_shape.orient_for_print(b)
                    else:
                        oriented = None
        except Exception:
            oriented = None

        if oriented is None:
            return None
    else:
        # model_or_shape is already a Shape or dict[str, Shape]
        oriented = model_or_shape
        if m_name is None:
            m_name = "model"

    target = Path(output_path_or_dir)

    # Multi-part assembly
    if isinstance(oriented, dict):
        results: dict[str, Path] = {}
        if target.suffix.lower() == ".stl":
            base_dir = target.parent
            base_stem = target.stem
        else:
            base_dir = target
            base_stem = config_name

        base_dir.mkdir(parents=True, exist_ok=True)
        for part_key, part_shape in oriented.items():
            part_path = base_dir / f"{base_stem}_{part_key}_print.stl"
            part_cfg = f"{config_name}_{part_key}_print"
            export_canonical_stl(
                part_shape,
                part_path,
                model_name=m_name,
                config_name=part_cfg,
                linear_deflection=linear_deflection,
                angular_deflection=angular_deflection,
            )
            results[part_key] = part_path
        return results

    # Single shape
    if isinstance(oriented, (Part, Solid, Compound, Shape)):
        if target.suffix.lower() == ".stl":
            final_path = target
        else:
            target.mkdir(parents=True, exist_ok=True)
            final_path = target / f"{config_name}_print.stl"

        cfg = config_name if config_name.endswith("_print") else f"{config_name}_print"
        return export_canonical_stl(
            oriented,
            final_path,
            model_name=m_name,
            config_name=cfg,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
        )

    return None
