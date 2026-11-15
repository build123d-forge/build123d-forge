"""Declarative, immutable base prototype for parametric build123d models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Literal, Self, Union, get_args, get_origin

from build123d.topology import Compound, Part, Shape
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.fields import FieldInfo

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
        """Generate documented TOML preset template from prototype parameter definitions."""
        lines: list[str] = []
        if schema_url:
            lines.append(f"#:schema {schema_url}\n")
        lines.append(f"# Configuration preset for {cls.__name__}")
        if cls.__doc__:
            lines.append(f"# {cls.__doc__.strip()}\n")

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
            lines.append(f"# {desc}{unit_str}{con_str}")

            val = field.default
            if val is ...:
                lines.append(f"# {name} = <required>\n")
            elif isinstance(val, str):
                lines.append(f'{name} = "{val}"\n')
            elif isinstance(val, bool):
                lines.append(f'{name} = {"true" if val else "false"}\n')
            elif isinstance(val, (int, float)):
                lines.append(f"{name} = {val}\n")
            else:
                lines.append(f"{name} = {val!r}\n")

        return "\n".join(lines)

    @classmethod
    def part_info_dict(cls) -> dict[str, Any]:
        """Derive build123d-mcp compatible PART_INFO dictionary from prototype fields."""
        params: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            ann = field.annotation
            origin = get_origin(ann)
            if origin is Literal:
                args = get_args(ann)
                t_name = type(args[0]).__name__ if args else "str"
            elif origin is Union:
                args = [a for a in get_args(ann) if a is not type(None)]
                t_name = args[0].__name__ if len(args) == 1 else "Union"
            else:
                t_name = getattr(ann, "__name__", str(ann))

            extra = field.json_schema_extra or {}
            if callable(extra):
                extra = extra({})
            unit = extra.get("unit") if isinstance(extra, dict) else None

            desc = field.description or name
            if unit and f"({unit})" not in desc and f"[{unit}]" not in desc:
                desc = f"{desc} ({unit})".strip()

            param_entry: dict[str, Any] = {
                "type": t_name,
                "default": field.default,
                "description": desc,
            }
            if unit:
                param_entry["unit"] = unit
            if field.gt is not None:
                param_entry["gt"] = field.gt
            if field.ge is not None:
                param_entry["ge"] = field.ge
            if field.lt is not None:
                param_entry["lt"] = field.lt
            if field.le is not None:
                param_entry["le"] = field.le
            if origin is Literal:
                param_entry["choices"] = list(get_args(ann))

            params[name] = param_entry

        return {
            "description": (cls.__doc__ or "").strip(),
            "tags": getattr(cls, "__tags__", ["build123d", "forge"]),
            "parameters": params,
        }

    @classmethod
    def from_toml(cls, path: Path | str, configs_dir: Path | None = None) -> Self:
        """Load and instantiate prototype from a TOML configuration preset file."""
        from build123d_forge.config import load_config_file

        return load_config_file(path, cls, configs_dir=configs_dir)
