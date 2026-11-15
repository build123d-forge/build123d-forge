"""TOML configuration loading, recursive extends inheritance, cycle detection, and typo diagnostics."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
import tomllib
import types
from typing import Annotated, Any, Literal, Sequence, TypeVar, Union, get_args, get_origin
import typing

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticUndefined

# Directives consumed by the configuration loader and stripped before Pydantic validation
DIRECTIVES: frozenset[str] = frozenset({"extends", "model", "$schema"})

# Common domain synonyms mapped to canonical parameter names
DEFAULT_SYNONYMS: dict[str, str] = {
    "glass_width": "wall_gap",
    "glass_thickness": "wall_gap",
    "slot_gap": "wall_gap",
    "slot_width": "wall_gap",
}

T = TypeVar("T", bound=BaseModel)


class ConfigError(Exception):
    """Raised when configuration loading, inheritance resolution, or validation fails."""

    def __init__(
        self,
        message: str,
        path: Path | str | None = None,
        cause: Exception | None = None,
        validation_error: ValidationError | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = Path(path) if path is not None else None
        self.cause = cause
        self.validation_error = validation_error or (cause if isinstance(cause, ValidationError) else None)

    def __str__(self) -> str:
        if self.path is not None:
            path_str = str(self.path)
            if not self.message.startswith(path_str):
                return f"{path_str}: {self.message}"
        return self.message


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override dictionary into base dictionary.

    Nested dictionaries are merged recursively. All other values (scalars, lists,
    sets) in override replace values in base. Returns a new merged dictionary.
    """
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_path(
    target: str | Path,
    base_dir: Path,
    configs_dir: Path | None = None,
) -> Path:
    """Resolve a target file path relative to base_dir or fallback to configs_dir."""
    cand = Path(target)
    if cand.is_absolute():
        return cand.resolve()

    # Priority 1: Relative to current config file's directory
    rel_cand = (base_dir / cand).resolve()
    if rel_cand.is_file():
        return rel_cand

    # Priority 2: Relative to project configs_dir if provided
    if configs_dir is not None:
        cfg_cand = (Path(configs_dir) / cand).resolve()
        if cfg_cand.is_file():
            return cfg_cand

    # Default to relative candidate for clear error reporting
    return rel_cand


def _resolve_config_raw(
    file_path: Path,
    chain: list[Path],
    configs_dir: Path | None = None,
) -> dict[str, Any]:
    """Recursively resolve a TOML configuration file and its extends chain."""
    resolved_path = file_path.resolve()
    if not resolved_path.is_file():
        raise ConfigError(
            f"Configuration file '{file_path}' not found (file does not exist at '{resolved_path}').",
            path=file_path,
        )

    try:
        content = resolved_path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
    except OSError as err:
        raise ConfigError(
            f"Failed to read config file '{file_path}': {err}",
            path=file_path,
            cause=err,
        ) from err
    except Exception as err:
        raise ConfigError(
            f"Failed to parse TOML configuration '{file_path}': {err}",
            path=file_path,
            cause=err,
        ) from err

    extends_val = data.get("extends")
    if extends_val is None:
        return data

    if not isinstance(extends_val, str) or not extends_val.strip():
        raise ConfigError(
            f"Invalid 'extends' directive in '{file_path}': expected non-empty string, got {type(extends_val).__name__}",
            path=file_path,
        )

    parent_path = _resolve_path(extends_val.strip(), resolved_path.parent, configs_dir=configs_dir)

    if not parent_path.is_file():
        raise ConfigError(
            f"Parent config file '{extends_val}' not found (file does not exist at '{parent_path}').",
            path=file_path,
        )

    if parent_path in chain:
        cycle_start = chain.index(parent_path)
        cycle_chain = chain[cycle_start:] + [parent_path]
        cycle_str = " -> ".join(p.name for p in cycle_chain)
        full_str = " -> ".join(str(p) for p in cycle_chain)
        raise ConfigError(
            f"Circular extends loop detected: {cycle_str} (full path: {full_str})",
            path=file_path,
        )

    parent_data = _resolve_config_raw(parent_path, chain + [parent_path], configs_dir=configs_dir)
    return deep_merge(parent_data, data)


def _extract_models_from_annotation(ann: Any) -> list[type[BaseModel]]:
    """Recursively extract all BaseModel subclasses from a type annotation."""
    if ann is None:
        return []
    while get_origin(ann) is Annotated:
        ann = get_args(ann)[0]
    origin = get_origin(ann)
    union_types = (Union, types.UnionType) if hasattr(types, "UnionType") else (Union,)
    if origin in union_types:
        result: list[type[BaseModel]] = []
        for a in get_args(ann):
            result.extend(_extract_models_from_annotation(a))
        return result
    if origin in (list, tuple, set, frozenset, dict):
        result = []
        for a in get_args(ann):
            result.extend(_extract_models_from_annotation(a))
        return result
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return [ann]
    return []


def _matches_model(cand: type[BaseModel], name: str) -> bool:
    """Check whether a model candidate matches a location segment name or discriminator."""
    if cand.__name__ == name or cand.__name__.lower() == name.lower():
        return True
    if hasattr(cand, "model_fields"):
        for field_info in cand.model_fields.values():
            if field_info.default == name:
                return True
            ann = field_info.annotation
            while get_origin(ann) is Annotated:
                ann = get_args(ann)[0]
            if get_origin(ann) is Literal and name in get_args(ann):
                return True
    return False


def _resolve_model_for_loc(
    root_cls: type[BaseModel],
    loc: tuple[Union[str, int], ...],
) -> tuple[type[BaseModel] | None, str]:
    """Traverse nested model fields along Pydantic error loc to find the target submodel class."""
    if not loc:
        return root_cls, ""
    bad_key = str(loc[-1])
    curr: type[BaseModel] | None = root_cls
    union_candidates: list[type[BaseModel]] = []

    for segment in loc[:-1]:
        seg_str = str(segment)

        # 1. Match segment against active union candidates
        if union_candidates:
            matched = [c for c in union_candidates if _matches_model(c, seg_str)]
            if matched:
                curr = matched[0]
                union_candidates = []
                continue
            # If segment is not a class/discriminator name, check if it matches a field on one candidate
            matched_field = [c for c in union_candidates if hasattr(c, "model_fields") and seg_str in c.model_fields]
            if matched_field:
                curr = matched_field[0]
                union_candidates = []
            else:
                union_candidates = []

        # 2. Segment matches current class name (e.g. redundant class name segment)
        if curr is not None and (curr.__name__ == seg_str or curr.__name__.lower() == seg_str.lower()):
            continue

        # 3. Numeric index for list/collection items
        if isinstance(segment, int) or seg_str.isdigit():
            continue

        # 4. Resolve field on current model
        if curr is None or not hasattr(curr, "model_fields"):
            return None, bad_key

        field_info = curr.model_fields.get(seg_str)
        if not field_info:
            return None, bad_key

        # 5. Extract submodels from field annotation
        sub_models = _extract_models_from_annotation(field_info.annotation)
        if not sub_models:
            curr = None
            union_candidates = []
        elif len(sub_models) == 1:
            curr = sub_models[0]
            union_candidates = sub_models
        else:
            curr = sub_models[0]
            union_candidates = sub_models

    # Disambiguate remaining union candidates using bad_key if unconsumed
    if len(union_candidates) > 1:
        best_cand = None
        best_score = 0.0
        for cand in union_candidates:
            if hasattr(cand, "model_fields"):
                for f in cand.model_fields:
                    if f.lower() == bad_key.lower():
                        best_cand = cand
                        best_score = 1.0
                        break
                    ratio = difflib.SequenceMatcher(None, bad_key.lower(), f.lower()).ratio()
                    if ratio > best_score:
                        best_score = ratio
                        best_cand = cand
        if best_cand and best_score >= 0.5:
            curr = best_cand

    return curr, bad_key


def get_typo_suggestion(
    unknown_key: str,
    candidates: list[str],
    *,
    model_cls: type[BaseModel] | None = None,
    cutoff: float = 0.5,
) -> str | None:
    """Find the best fuzzy matching candidate field name for an unknown configuration key.

    Returns the matching field name in the exact casing defined on the model,
    or None if no candidate exceeds the similarity cutoff.
    """
    key_clean = unknown_key.strip()
    key_lower = key_clean.lower()

    # 1. Check optional domain synonyms defined on prototype class
    if model_cls is not None and hasattr(model_cls, "__synonyms__") and isinstance(model_cls.__synonyms__, dict):
        if key_clean in model_cls.__synonyms__:
            return str(model_cls.__synonyms__[key_clean])
        if key_lower in model_cls.__synonyms__:
            return str(model_cls.__synonyms__[key_lower])

    # 2. Check default domain synonyms if target candidate is present
    if key_lower in DEFAULT_SYNONYMS:
        target = DEFAULT_SYNONYMS[key_lower]
        for c in candidates:
            if c.lower() == target.lower():
                return c

    # 3. Case-insensitive lookup map preserving original field casing
    candidate_map = {c.lower(): c for c in candidates}

    # Minimum length guard for fuzzy matching: short keys (< 3 chars) cannot fuzzy match
    if len(key_lower) < 3:
        return candidate_map.get(key_lower)

    # Disallow fuzzy matching against short candidates (< 3 chars) to prevent ratio inflation
    fuzzy_candidates = [c for c in candidate_map if len(c) >= 3]

    # 4. Fuzzy match via difflib SequenceMatcher
    matches = difflib.get_close_matches(
        key_lower,
        fuzzy_candidates,
        n=1,
        cutoff=cutoff,
    )
    if matches:
        return candidate_map[matches[0]]

    # 5. Prefix/suffix fallback for truncated or extended names with candidate and delta length guards
    for c_low, c_orig in candidate_map.items():
        if len(c_low) >= 3 and len(key_lower) >= 3 and abs(len(key_lower) - len(c_low)) <= 4:
            if c_low.startswith(key_lower) or key_lower.startswith(c_low):
                return c_orig

    return None


def _format_validation_error(
    model_cls: type[BaseModel],
    exc: ValidationError,
    path: Path | None = None,
    cutoff: float = 0.5,
) -> str:
    """Format Pydantic ValidationError into user-friendly message with typo suggestions."""
    formatted_errors: list[str] = []

    for err in exc.errors():
        err_type = err.get("type", "")
        loc = err.get("loc", ())

        if err_type == "extra_forbidden":
            target_cls, bad_key = _resolve_model_for_loc(model_cls, loc)
            candidates = (
                list(target_cls.model_fields.keys())
                if target_cls and hasattr(target_cls, "model_fields")
                else list(model_cls.model_fields.keys())
            )
            suggestion = get_typo_suggestion(
                bad_key,
                candidates,
                model_cls=target_cls or model_cls,
                cutoff=cutoff,
            )
            loc_prefix = f"{'.'.join(str(k) for k in loc[:-1])}: " if len(loc) > 1 else ""
            if suggestion:
                formatted_errors.append(
                    f"{loc_prefix}Extra inputs are not permitted ('{bad_key}' — did you mean '{suggestion}'?)"
                )
            else:
                formatted_errors.append(f"{loc_prefix}Extra inputs are not permitted ('{bad_key}')")
        else:
            loc_str = ".".join(str(k) for k in loc) if loc else ""
            msg = err.get("msg", "Validation error")
            formatted_errors.append(f"{loc_str}: {msg}" if loc_str else msg)

    return "; ".join(formatted_errors)


def read_config_file(
    path: Path | str,
    configs_dir: Path | str | None = None,
    strip_directives: bool = False,
) -> dict[str, Any]:
    """Read a TOML config preset, resolving extends chains recursively.

    Args:
        path: Path to the .toml file.
        configs_dir: Optional base directory for configuration files.
        strip_directives: If True, remove directives ('extends', 'model', '$schema').

    Returns:
        Merged parameter dictionary.
    """
    file_path = Path(path)
    cfg_dir = Path(configs_dir) if configs_dir is not None else None

    # If file not found directly and configs_dir is provided, try configs_dir / path
    if not file_path.is_file() and cfg_dir is not None:
        cand = (cfg_dir / file_path).resolve()
        if cand.is_file():
            file_path = cand

    data = _resolve_config_raw(file_path, [file_path.resolve()], configs_dir=cfg_dir)
    if strip_directives:
        return {k: v for k, v in data.items() if k not in DIRECTIVES}
    return data


def load_config_file(
    path: Path | str,
    model_cls: type[T],
    configs_dir: Path | str | None = None,
) -> T:
    """Load a TOML configuration preset, resolve inheritance, and instantiate model_cls.

    Args:
        path: Path to the .toml file.
        model_cls: ModelPrototype (or Pydantic BaseModel) class to validate against.
        configs_dir: Optional base directory for configuration preset resolution.

    Returns:
        Validated model_cls instance.

    Raises:
        ConfigError: On missing files, circular extends loops, syntax errors,
            or parameter validation errors (including typo diagnostics).
    """
    if not str(path).strip():
        raise ConfigError("Configuration file path cannot be empty.", path=path)

    file_path = Path(path)
    cfg_dir = Path(configs_dir) if configs_dir is not None else None

    # Load resolved data and strip loader directives
    raw_data = read_config_file(file_path, configs_dir=cfg_dir, strip_directives=False)
    clean_data = {k: v for k, v in raw_data.items() if k not in DIRECTIVES}

    try:
        return model_cls.model_validate(clean_data)
    except ValidationError as exc:
        msg = _format_validation_error(model_cls, exc, path=file_path, cutoff=0.5)
        raise ConfigError(msg, path=file_path, cause=exc, validation_error=exc) from exc


def discover_config_files(configs_dir: Path | str = "configs") -> list[Path]:
    """Discover all .toml configuration preset files within a directory, sorted by name."""
    cfg_path = Path(configs_dir)
    if not cfg_path.is_dir():
        return []
    return sorted(p for p in cfg_path.glob("*.toml") if p.is_file())


def _format_toml_value(val: Any) -> str:
    """Format a Python primitive or collection into valid TOML literal syntax."""
    if hasattr(val, "value"):
        return _format_toml_value(val.value)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val)
    if isinstance(val, Path):
        return json.dumps(str(val))
    if isinstance(val, (set, frozenset)):
        return _format_toml_value(sorted(val, key=repr))
    if isinstance(val, (list, tuple)):
        items = [_format_toml_value(item) for item in val if item is not None]
        return f"[{', '.join(items)}]"
    if isinstance(val, dict):
        items = [f"{json.dumps(str(k))} = {_format_toml_value(v)}" for k, v in val.items() if v is not None]
        return f"{{{', '.join(items)}}}"
    if val is None:
        return '""'
    try:
        return json.dumps(val)
    except Exception:
        return str(val)


def create_config_template(
    model_cls: type[BaseModel],
    schema_url: str | None = None,
    sparse: bool = True,
) -> str:
    """Generate documented TOML configuration text from prototype schema and defaults.

    Args:
        model_cls: ModelPrototype (or BaseModel) subclass.
        schema_url: Optional schema header URL.
        sparse: If True, provides documented preset template with defaults.

    Returns:
        Valid, parseable TOML template string.
    """
    if hasattr(model_cls, "to_toml_template") and callable(model_cls.to_toml_template):
        return model_cls.to_toml_template(schema_url=schema_url)

    lines: list[str] = []
    if schema_url:
        lines.append(f"#:schema {schema_url}\n")
    lines.append(f"# Configuration preset for {getattr(model_cls, '__name__', 'Model')}")
    if model_cls.__doc__:
        for doc_line in model_cls.__doc__.strip().splitlines():
            d = doc_line.strip()
            lines.append(f"# {d}" if d else "#")
        lines.append("")

    if hasattr(model_cls, "model_fields"):
        for name, field in model_cls.model_fields.items():
            desc = field.description or name
            for d in desc.strip().splitlines():
                d_clean = d.strip()
                lines.append(f"# {d_clean}" if d_clean else "#")

            is_req = field.is_required() if hasattr(field, "is_required") else False
            val = field.default

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
    return "\n".join(lines).rstrip() + "\n"


def scaffold_config(
    model_cls: type[BaseModel],
    destination_path: Path | str,
    force: bool = False,
    sparse: bool = True,
    schema_url: str | None = None,
) -> Path:
    """Create a new documented .toml configuration file from a ModelPrototype.

    Args:
        model_cls: ModelPrototype subclass.
        destination_path: Target .toml file path (appends .toml if missing).
        force: If True, overwrites destination_path if it already exists.
        sparse: If True, generates sparse documented template.
        schema_url: Optional schema header URL.

    Returns:
        Path pointing to the written configuration file.

    Raises:
        FileExistsError: If destination_path exists and force is False.
    """
    dest = Path(destination_path)
    if not dest.name.lower().endswith(".toml"):
        dest = dest.with_name(f"{dest.name}.toml")

    if dest.exists() and not force:
        raise FileExistsError(f"Configuration file '{dest}' already exists. Use --force to overwrite.")

    content = create_config_template(model_cls, schema_url=schema_url, sparse=sparse)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest
