"""Compatibility shim for build123d-mcp and legacy procedural part scripts."""

from __future__ import annotations

import ast
import inspect
import pprint
from typing import Any

from build123d.topology import Compound, Part, Shape
from pydantic import ValidationError
from build123d_forge.prototype import _build_part_info, _resolve_type_name, _to_safe_literal


def generate_part_info(cls: type[Any]) -> dict[str, Any]:
    """Derive build123d-mcp compatible PART_INFO dictionary from model prototype fields."""
    if hasattr(cls, "part_info_dict") and callable(getattr(cls, "part_info_dict")):
        return cls.part_info_dict()
    return _build_part_info(cls)


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

    # Rebuild Pydantic core schema using caller module namespace to resolve ForwardRefs
    # (e.g. when 'from __future__ import annotations' is used and class is defined in an exec session)
    if hasattr(cls, "model_rebuild") and callable(getattr(cls, "model_rebuild")):
        try:
            cls.model_rebuild(_types_namespace=module_globals)
        except Exception:
            pass

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
        raw_shape = instance.build()

        orient_fn = getattr(instance, "orient_for_print", None)
        if orient_fn is not None and callable(orient_fn):
            sig = inspect.signature(orient_fn)
            params = [
                p
                for p in sig.parameters.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and p.name not in ("self", "cls")
            ]
            oriented = orient_fn(raw_shape) if len(params) >= 1 else orient_fn()
            if oriented is not None:
                if isinstance(oriented, dict):
                    return Compound(children=list(oriented.values()))
                return oriented

        if isinstance(raw_shape, dict):
            return Compound(children=list(raw_shape.values()))
        return raw_shape

    make.__doc__ = cls.__doc__ or f"Generate {cls.__name__} CAD geometry."
    make.__name__ = "make"
    make_print_oriented.__doc__ = (
        "Generate print-bed oriented CAD geometry (coplanar at Z=0 for FDM printing)."
    )
    make_print_oriented.__name__ = "make_print_oriented"

    module_globals["make"] = make
    module_globals["make_print_oriented"] = make_print_oriented

    # Hook for execute_file inside build123d-mcp or interactive execution
    if module_globals.get("__name__") in ("builtins", None, "build123d_mcp") or "show" in module_globals:
        try:
            module_globals["result"] = make()
        except ValidationError:
            pass
