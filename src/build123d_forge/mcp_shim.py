"""Compatibility shim for build123d-mcp and legacy procedural part scripts."""

from __future__ import annotations

import ast
import inspect
import pprint
import typing
from typing import Any, get_args, get_origin

from build123d.topology import Compound, Part, Shape


def _resolve_type_name(annotation: Any) -> str:
    """Derive human-readable type string from a Python type annotation."""
    origin = get_origin(annotation)
    if origin is typing.Literal:
        args = get_args(annotation)
        if args:
            return type(args[0]).__name__
        return "str"
    if origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _resolve_type_name(args[0])
        return "Union[" + ", ".join(_resolve_type_name(a) for a in args) + "]"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def generate_part_info(cls: type[Any]) -> dict[str, Any]:
    """Derive build123d-mcp compatible PART_INFO dictionary from model prototype fields."""
    if hasattr(cls, "part_info_dict") and callable(getattr(cls, "part_info_dict")):
        return cls.part_info_dict()

    parameters: dict[str, dict[str, Any]] = {}
    model_fields = getattr(cls, "model_fields", {})

    for name, field in model_fields.items():
        type_str = _resolve_type_name(field.annotation)
        extra = field.json_schema_extra or {}
        if callable(extra):
            extra = extra({})
        unit = extra.get("unit") if isinstance(extra, dict) else None

        desc = field.description or name
        if unit and f"({unit})" not in desc and f"[{unit}]" not in desc:
            desc = f"{desc} ({unit})".strip()

        origin = get_origin(field.annotation)
        choices = list(get_args(field.annotation)) if origin is typing.Literal else None

        param_entry: dict[str, Any] = {
            "type": type_str,
            "default": field.default,
            "description": desc,
        }
        if unit:
            param_entry["unit"] = unit
        if getattr(field, "gt", None) is not None:
            param_entry["gt"] = field.gt
        if getattr(field, "ge", None) is not None:
            param_entry["ge"] = field.ge
        if getattr(field, "lt", None) is not None:
            param_entry["lt"] = field.lt
        if getattr(field, "le", None) is not None:
            param_entry["le"] = field.le
        if choices:
            param_entry["choices"] = choices

        parameters[name] = param_entry

    return {
        "description": (cls.__doc__ or "").strip(),
        "tags": getattr(cls, "__tags__", ["build123d", "forge"]),
        "parameters": parameters,
    }


def generate_part_info_code(cls: type[Any], indent: int = 4) -> str:
    """Generate static Python source code for PART_INFO assignment."""
    info = generate_part_info(cls)
    formatted = pprint.pformat(info, indent=indent, width=88, sort_dicts=False)
    return f"PART_INFO = {formatted}\n"


def generate_part_info_ast(cls: type[Any]) -> ast.Assign:
    """Generate an ast.Assign node representing static PART_INFO = {...}."""
    code = generate_part_info_code(cls)
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            return node
    raise RuntimeError("Failed to parse generated PART_INFO code into ast.Assign")


def mcp_shim(
    cls: type[Any],
    module_globals: dict[str, Any],
    *,
    inject_defaults: bool = False,
) -> None:
    """Shim a build123d-forge prototype into build123d-mcp and legacy module namespaces.

    Args:
        cls: ModelPrototype subclass.
        module_globals: Module global dictionary (typically globals()).
        inject_defaults: If True, populates parameter defaults as module globals.
    """
    if not isinstance(cls, type):
        raise TypeError(f"mcp_shim expects a class type, got {cls!r}")
    if not hasattr(cls, "model_fields") or not hasattr(cls, "model_validate"):
        raise TypeError(f"mcp_shim expects a ModelPrototype (or Pydantic BaseModel) subclass, got {cls!r}")
    if not isinstance(module_globals, dict):
        raise TypeError(f"module_globals must be a dict, got {type(module_globals).__name__}")

    part_info = generate_part_info(cls)
    module_globals["PART_INFO"] = part_info

    if inject_defaults:
        for name, spec in part_info.get("parameters", {}).items():
            if name not in module_globals:
                module_globals[name] = spec.get("default")

    def make(**kwargs: Any) -> Shape:
        """Instantiate prototype with overrides and build Part or Compound."""
        instance = cls.model_validate(kwargs)
        shape = instance.build()
        if isinstance(shape, dict):
            return Compound(children=list(shape.values()))
        return shape

    def make_print_oriented(**kwargs: Any) -> Shape:
        """Instantiate prototype, build Part, and return print-oriented geometry."""
        instance = cls.model_validate(kwargs)
        shape = instance.build()
        if isinstance(shape, dict):
            shape = Compound(children=list(shape.values()))

        orient_fn = getattr(instance, "orient_for_print", None)
        if orient_fn is not None and callable(orient_fn):
            sig = inspect.signature(orient_fn)
            params = [
                p
                for p in sig.parameters.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and p.name not in ("self", "cls")
            ]
            oriented = orient_fn(shape) if len(params) >= 1 else orient_fn()
            if oriented is not None:
                if isinstance(oriented, dict):
                    return Compound(children=list(oriented.values()))
                return oriented

        return shape

    make.__doc__ = cls.__doc__ or f"Generate {cls.__name__} CAD geometry."
    make.__name__ = "make"
    make_print_oriented.__doc__ = (
        "Generate print-bed oriented CAD geometry (coplanar at Z=0 for FDM printing)."
    )
    make_print_oriented.__name__ = "make_print_oriented"

    module_globals["make"] = make
    module_globals["make_print_oriented"] = make_print_oriented

    # Hook for execute_file inside build123d-mcp
    if module_globals.get("__name__") == "builtins":
        module_globals["result"] = make()
