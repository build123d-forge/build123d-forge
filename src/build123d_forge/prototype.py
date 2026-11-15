"""Declarative, immutable base prototype for parametric build123d models."""

from __future__ import annotations

from abc import ABC, abstractmethod
import enum
import json
import math
import os
from pathlib import Path
import types
from typing import Any, ClassVar, Literal, Self, Union, get_args, get_origin

from build123d.topology import Compound, Part, Shape
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

# Enhance FieldInfo with properties for bounds and unit metadata
if not hasattr(FieldInfo, "gt"):
    FieldInfo.gt = property(
        lambda self: next((m.gt for m in getattr(self, "metadata", []) if hasattr(m, "gt")), None)
    )
if not hasattr(FieldInfo, "ge"):
    FieldInfo.ge = property(
        lambda self: next((m.ge for m in getattr(self, "metadata", []) if hasattr(m, "ge")), None)
    )
if not hasattr(FieldInfo, "lt"):
    FieldInfo.lt = property(
        lambda self: next((m.lt for m in getattr(self, "metadata", []) if hasattr(m, "lt")), None)
    )
if not hasattr(FieldInfo, "le"):
    FieldInfo.le = property(
        lambda self: next((m.le for m in getattr(self, "metadata", []) if hasattr(m, "le")), None)
    )
if not hasattr(FieldInfo, "unit"):
    FieldInfo.unit = property(
        lambda self: self.json_schema_extra.get("unit")
        if isinstance(getattr(self, "json_schema_extra", None), dict)
        else None
    )


def Param(
    default: Any = ...,
    *,
    unit: str | None = None,
    description: str | None = None,
    gt: float | None = None,
    ge: float | None = None,
    lt: float | None = None,
    le: float | None = None,
    json_schema_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Convenience wrapper over pydantic.Field storing physical units in json_schema_extra."""
    extra = dict(json_schema_extra) if json_schema_extra else {}
    if unit is not None:
        extra["unit"] = unit
    return Field(
        default,
        description=description,
        gt=gt,
        ge=ge,
        lt=lt,
        le=le,
        json_schema_extra=extra if extra else None,
        **kwargs,
    )


class ViewSpec(BaseModel):
    """Specification of a camera view for rendering 2D previews."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stem: str
    description: str
    direction: str = "front"
    azimuth: float = 0.0
    elevation: float = 0.0
    use_print_oriented: bool = False

    def __init__(
        self,
        stem: str | None = None,
        description: str | None = None,
        direction: str = "front",
        azimuth: float = 0.0,
        elevation: float = 0.0,
        use_print_oriented: bool = False,
        **kwargs: Any,
    ) -> None:
        data = dict(kwargs)
        if stem is not None:
            data["stem"] = stem
        if description is not None:
            data["description"] = description
        if "direction" not in data:
            data["direction"] = direction
        if "azimuth" not in data:
            data["azimuth"] = azimuth
        if "elevation" not in data:
            data["elevation"] = elevation
        if "use_print_oriented" not in data:
            data["use_print_oriented"] = use_print_oriented
        super().__init__(**data)


class ValidationReport(BaseModel):
    """B-Rep geometric verification report for watertightness and topological checks."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    is_manifold: bool
    watertight: bool
    solids: int
    faces: int
    volume_mm3: float
    bounding_box_mm: tuple[float, float, float]
    errors: list[str] = Field(default_factory=list)

def _format_toml_value(val: Any) -> str:
    """Format a Python primitive or collection into valid TOML literal syntax."""
    if isinstance(val, enum.Enum):
        return _format_toml_value(val.value)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val)
    if hasattr(val, "model_dump") and callable(getattr(val, "model_dump")) and not isinstance(val, type):
        try:
            return _format_toml_value(val.model_dump())
        except Exception:
            pass
    if isinstance(val, (Path, os.PathLike)):
        return json.dumps(str(val))
    if isinstance(val, (set, frozenset)):
        return _format_toml_value(sorted(val, key=repr))
    if isinstance(val, (list, tuple)):
        items = [_format_toml_value(item) for item in val if item is not None]
        delim = ", "
        return f"[{delim.join(items)}]"
    if isinstance(val, dict):
        items = [
            f"{json.dumps(str(k.value if isinstance(k, enum.Enum) else k))} = {_format_toml_value(v)}"
            for k, v in val.items()
            if v is not None
        ]
        delim = ", "
        return f"{{{delim.join(items)}}}"
    if val is None:
        return '""'
    try:
        return json.dumps(val)
    except Exception:
        return repr(val)


def _to_safe_literal(val: Any) -> Any:
    """Convert any Python value into a standard literal safe for ast.literal_eval and json.dumps."""
    if val is None or val is ... or type(val).__name__ == "PydanticUndefinedType":
        return None
    if isinstance(val, enum.Enum):
        return _to_safe_literal(val.value)
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if math.isinf(val) or math.isnan(val):
            return None
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, (Path, os.PathLike)):
        return str(val)
    if hasattr(val, "model_dump") and callable(getattr(val, "model_dump")) and not isinstance(val, type):
        try:
            return _to_safe_literal(val.model_dump())
        except Exception:
            return None
    if isinstance(val, dict):
        return {
            (str(_to_safe_literal(k)) if isinstance(k, enum.Enum) else str(k)): _to_safe_literal(v)
            for k, v in val.items()
        }
    if isinstance(val, (list, tuple)):
        return [_to_safe_literal(item) for item in val]
    if isinstance(val, (set, frozenset)):
        return [_to_safe_literal(item) for item in sorted(val, key=repr)]
    return None


def _resolve_type_name(annotation: Any) -> str:
    """Derive human-readable type string from a Python type annotation supporting PEP 604 unions."""
    origin = get_origin(annotation)
    if origin is Literal:
        args = get_args(annotation)
        if args:
            return type(args[0]).__name__
        return "str"
    if origin in (Union, getattr(types, "UnionType", None)):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _resolve_type_name(args[0])
        return "Union[" + ", ".join(_resolve_type_name(a) for a in args) + "]"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _build_part_info(cls: type[Any]) -> dict[str, Any]:
    """Derive build123d-mcp compatible PART_INFO dictionary guaranteed to contain only literals."""
    parameters: dict[str, dict[str, Any]] = {}
    model_fields = getattr(cls, "model_fields", {})

    for name, field in model_fields.items():
        ann = field.annotation
        type_str = _resolve_type_name(ann)
        origin = get_origin(ann)

        choices = None
        if origin is Literal:
            choices = list(get_args(ann))
        elif origin in (Union, getattr(types, "UnionType", None)):
            non_none = [a for a in get_args(ann) if a is not type(None)]
            if len(non_none) == 1 and get_origin(non_none[0]) is Literal:
                choices = list(get_args(non_none[0]))

        desc = field.description or name
        extra = getattr(field, "json_schema_extra", None) or {}
        if callable(extra):
            extra = extra({})
        unit = extra.get("unit") if isinstance(extra, dict) else None
        if unit and f"({unit})" not in desc and f"[{unit}]" not in desc:
            desc = f"{desc} ({unit})".strip()

        # Determine requirement and safe default
        is_req = False
        if hasattr(field, "is_required") and callable(field.is_required):
            is_req = field.is_required()
        elif field.default is ... or type(field.default).__name__ == "PydanticUndefinedType":
            is_req = getattr(field, "default_factory", None) is None

        if is_req:
            safe_default = None
        else:
            raw_val = field.default
            if (raw_val is ... or type(raw_val).__name__ == "PydanticUndefinedType") and callable(
                getattr(field, "default_factory", None)
            ):
                try:
                    raw_val = field.default_factory()
                except Exception:
                    raw_val = None
            safe_default = _to_safe_literal(raw_val)

        param_entry: dict[str, Any] = {
            "type": type_str,
            "default": safe_default,
            "description": desc,
        }
        if unit:
            param_entry["unit"] = str(unit)
        for bound_key in ("gt", "ge", "lt", "le"):
            bound_val = getattr(field, bound_key, None)
            if bound_val is not None:
                safe_bound = _to_safe_literal(bound_val)
                if safe_bound is not None:
                    param_entry[bound_key] = safe_bound
        if choices:
            param_entry["choices"] = [_to_safe_literal(c) for c in choices]

        parameters[name] = param_entry

    return {
        "description": (cls.__doc__ or "").strip(),
        "tags": [str(t) for t in getattr(cls, "__tags__", ["build123d", "forge"])],
        "parameters": parameters,
    }


class ModelPrototype(BaseModel, ABC):
    """Declarative, immutable base prototype for parametric build123d models."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
        validate_default=True,
    )

    @abstractmethod
    def build(self) -> Part | dict[str, Part]:
        """Generate CAD geometry using build123d primitives and operations.

        Must return either a single Part solid or a dictionary mapping part
        names to Part solids (for multi-part assemblies).
        """
        ...

    def orient_for_print(self, part: Part | None = None) -> Part | dict[str, Part] | None:
        """Optional hook: transform built solid for optimal FDM 3D printing.

        Base implementation returns None (no specialized print orientation).
        Subclasses may override this with either 0 arguments or 1 argument (`part`).
        """
        return None

    def views(self) -> list[ViewSpec]:
        """Optional hook: camera suite for preview renders. Default is standard 4-view suite."""
        return [
            ViewSpec(stem="iso", description="Isometric front view", direction="front", azimuth=35.0, elevation=25.0),
            ViewSpec(stem="top", description="Top view", direction="top", azimuth=0.0, elevation=90.0),
            ViewSpec(stem="side", description="Side view", direction="side", azimuth=90.0, elevation=0.0),
            ViewSpec(stem="bed", description="Print bed view", direction="iso", azimuth=35.0, elevation=30.0, use_print_oriented=True),
        ]

    def verify(self, shape: Shape | dict[str, Shape]) -> ValidationReport:
        """Run B-Rep validity and manifoldness checks on generated CAD solid(s)."""
        if isinstance(shape, dict):
            shapes = list(shape.values())
            labels = list(shape.keys())
        elif isinstance(shape, (list, tuple)):
            shapes = list(shape)
            labels = [getattr(s, "label", f"shape_{i}") or f"shape_{i}" for i, s in enumerate(shapes)]
        else:
            shapes = [shape]
            labels = [getattr(shape, "label", "shape") or "shape"]

        errors: list[str] = []
        total_solids = 0
        total_faces = 0
        total_volume = 0.0
        all_valid = True
        all_manifold = True

        for s, label in zip(shapes, labels):
            lbl = label or getattr(s, "label", "shape") or "shape"
            is_valid = getattr(s, "is_valid", False)
            if not is_valid:
                all_valid = False
                errors.append(f"Shape '{lbl}' failed B-Rep validity check (is_valid=False)")

            is_manifold = getattr(s, "is_manifold", True)
            if not is_manifold:
                all_manifold = False
                errors.append(f"Shape '{lbl}' is non-manifold (is_manifold=False)")

            solids = s.solids() if hasattr(s, "solids") else []
            faces = s.faces() if hasattr(s, "faces") else []
            total_solids += len(solids)
            total_faces += len(faces)
            vol = getattr(s, "volume", 0.0)
            total_volume += vol

            if len(solids) == 0:
                errors.append(f"Shape '{lbl}' contains zero solids (open face or shell)")
            for solid in solids:
                if getattr(solid, "volume", 0.0) <= 0.0:
                    errors.append(f"Solid in '{lbl}' has non-positive volume: {solid.volume:.4f} mm³")

        bb = (0.0, 0.0, 0.0)
        if total_solids > 0:
            if len(shapes) > 1:
                combined = Compound(children=shapes)
            else:
                combined = shapes[0]
            size = combined.bounding_box().size
            bb = (round(size.X, 3), round(size.Y, 3), round(size.Z, 3))

        watertight = (
            all_valid
            and all_manifold
            and total_solids >= 1
            and total_volume > 0.0
            and len(errors) == 0
        )

        return ValidationReport(
            is_valid=all_valid,
            is_manifold=all_manifold,
            watertight=watertight,
            solids=total_solids,
            faces=total_faces,
            volume_mm3=round(total_volume, 3),
            bounding_box_mm=bb,
            errors=errors,
        )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Generate Draft 2020-12 JSON Schema for the prototype."""
        from build123d_forge.schema import generate_json_schema

        return generate_json_schema(cls, *args, **kwargs)

    @classmethod
    def to_toml_template(cls, schema_url: str | None = None) -> str:
        """Generate a documented, valid TOML configuration preset template for this prototype.

        Guarantees:
        - Required fields without defaults are commented as `# {name} = <required>`.
        - Optional fields with None defaults are commented as `# {name} = <optional>`.
        - Multi-line class docstrings and field descriptions are properly prefixed with '# '.
        - Field defaults (bool, int, float, str, list, tuple) are formatted as valid TOML literals.
        - The resulting template is 100% parseable by standard library `tomllib.loads()`.
        """
        lines: list[str] = []
        if schema_url:
            lines.append(f"#:schema {schema_url}\n")
        lines.append(f"# Configuration preset for {cls.__name__}")
        if cls.__doc__:
            for doc_line in cls.__doc__.strip().splitlines():
                doc_clean = doc_line.strip()
                lines.append(f"# {doc_clean}" if doc_clean else "#")
            lines.append("")

        for name, field in cls.model_fields.items():
            desc = field.description or name
            extra = field.json_schema_extra or {}
            if callable(extra):
                extra = extra({})
            unit = extra.get("unit") if isinstance(extra, dict) else None

            constraints: list[str] = []
            for m in getattr(field, "metadata", []):
                if hasattr(m, "gt") and m.gt is not None:
                    constraints.append(f"> {m.gt}")
                if hasattr(m, "ge") and m.ge is not None:
                    constraints.append(f">= {m.ge}")
                if hasattr(m, "lt") and m.lt is not None:
                    constraints.append(f"< {m.lt}")
                if hasattr(m, "le") and m.le is not None:
                    constraints.append(f"<= {m.le}")

            unit_str = f" [{unit}]" if unit else ""
            con_str = f" ({', '.join(constraints)})" if constraints else ""

            # Ensure all description lines are commented out
            desc_lines = [d.strip() for d in desc.strip().splitlines() if d.strip()]
            if not desc_lines:
                desc_lines = [name]
            for d in desc_lines[:-1]:
                lines.append(f"# {d}")
            lines.append(f"# {desc_lines[-1]}{unit_str}{con_str}")

            # Inspect field requirement and default
            is_req = field.is_required() if hasattr(field, "is_required") else False
            val = field.default

            # Resolve default_factory if present
            if is_req or val is ... or val is PydanticUndefined or type(val).__name__ == "PydanticUndefinedType":
                if getattr(field, "default_factory", None) is not None:
                    try:
                        val = field.default_factory()
                        is_req = False
                    except Exception:
                        pass

            if is_req or val is ... or val is PydanticUndefined or type(val).__name__ == "PydanticUndefinedType":
                lines.append(f"# {name} = <required>\n")
            elif val is None:
                lines.append(f"# {name} = <optional>\n")
            else:
                lines.append(f"{name} = {_format_toml_value(val)}\n")

        return "\n".join(lines)

    @classmethod
    def part_info_dict(cls) -> dict[str, Any]:
        """Derive build123d-mcp compatible PART_INFO dictionary from prototype fields."""
        return _build_part_info(cls)

    @classmethod
    def from_toml(cls, path: Path | str, configs_dir: Path | None = None) -> Self:
        """Load and instantiate prototype from a TOML configuration preset file."""
        from build123d_forge.config import load_config_file

        return load_config_file(path, cls, configs_dir=configs_dir)
