"""Build engine, parallel worker pool, model/config discovery, and B-Rep verification gates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import importlib.util
import inspect
import os
from pathlib import Path
import re
import shutil
import sys
import time
import tomllib
import types
from typing import Any, Sequence

from build123d import Compound, Part, Shape, Solid

from build123d_forge.cache import (
    BuildManifest,
    compute_manifest,
    find_project_root,
    is_cache_valid,
)
from build123d_forge.config import (
    ConfigError,
    discover_config_files,
    load_config_file,
    read_config_file,
)
from build123d_forge.exporters import (
    export_canonical_stl,
    export_deterministic_step,
    export_print_stl,
)
from build123d_forge.prototype import ModelPrototype, ValidationReport
from build123d_forge.render import render_preview_grid, render_views
from build123d_forge.schema import export_json_schema


def to_snake_case(name: str) -> str:
    """Convert CamelCase or PascalCase identifier to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


@dataclass(frozen=True)
class DiscoveredModel:
    """Represents a discovered ModelPrototype subclass and its source location."""

    name: str
    cls: type[ModelPrototype]
    source_file: Path

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.cls(*args, **kwargs)


@dataclass
class VariantSpec:
    """Specification of a buildable variant."""

    name: str
    model_cls: type[ModelPrototype]
    config_path: Path | None
    output_dir: Path
    source_file: Path

    @property
    def model(self) -> DiscoveredModel:
        return DiscoveredModel(
            name=self.model_cls.__name__,
            cls=self.model_cls,
            source_file=self.source_file,
        )


@dataclass
class BuildResult:
    """Outcome of building a single variant."""

    variant: str
    model: str
    output_dir: str
    cached: bool = False
    success: bool = True
    error: str | None = None
    elapsed_time: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    """Outcome of validating and verifying a single variant."""

    variant: str
    model: str
    success: bool
    config_valid: bool
    geometry_valid: bool
    errors: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None


@dataclass
class VariantStatus:
    """Status summary of a discovered variant for bforge list."""

    model_name: str
    variant_name: str
    config_file: str
    output_dir: str
    status: str  # "UP-TO-DATE", "STALE", or "UNBUILT"


# ==============================================================================
# Model & Config Discovery
# ==============================================================================

def load_project_settings(project_root: Path | None = None) -> dict[str, Any]:
    """Load [tool.build123d_forge] settings from pyproject.toml if present."""
    root = project_root or find_project_root(Path.cwd())
    pyproj = root / "pyproject.toml"
    if pyproj.is_file():
        try:
            with open(pyproj, "rb") as f:
                data = tomllib.load(f)
            return data.get("tool", {}).get("build123d_forge", {})
        except Exception:
            return {}
    return {}


def _safe_import_file(file_path: Path, project_root: Path) -> types.ModuleType | None:
    """Safely import a local Python file as a module."""
    try:
        abs_root = str(project_root.resolve())
        abs_parent = str(file_path.parent.resolve())
        if abs_root not in sys.path:
            sys.path.insert(0, abs_root)
        if abs_parent not in sys.path:
            sys.path.insert(0, abs_parent)

        mod_id = f"_bforge_mod_{file_path.stem}_{abs(hash(str(file_path.resolve()))) % 1000000}"
        spec = importlib.util.spec_from_file_location(mod_id, file_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_id] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def discover_models(
    search_dirs: Sequence[Path | str] | None = None,
    project_root: Path | None = None,
) -> dict[str, type[ModelPrototype]]:
    """Discover all concrete ModelPrototype subclasses across project source directories."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    settings = load_project_settings(root)

    dirs_to_search: list[Path] = []
    if search_dirs:
        dirs_to_search = [Path(d) if Path(d).is_absolute() else (root / d).resolve() for d in search_dirs]
    elif "models" in settings:
        for p in settings["models"]:
            dirs_to_search.append((root / p).resolve())
    else:
        # Standard conventions: models/ then parts/ then project root
        if (root / "models").is_dir():
            dirs_to_search.append(root / "models")
        if (root / "parts").is_dir():
            dirs_to_search.append(root / "parts")
        if not dirs_to_search:
            dirs_to_search.append(root)

    # Ensure project root is at front of sys.path
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    discovered: dict[str, type[ModelPrototype]] = {}
    ignored_dir_names = {".git", ".venv", "venv", "dist", "build", "__pycache__", ".pytest_cache", "tests"}

    candidate_files: list[Path] = []
    for d in dirs_to_search:
        if not d.exists():
            continue
        if d.is_file() and d.suffix == ".py":
            candidate_files.append(d)
        elif d.is_dir():
            for p in d.rglob("*.py"):
                if any(part in ignored_dir_names for part in p.parts):
                    continue
                if p.name.startswith("test_") or p.name.endswith("_test.py"):
                    continue
                candidate_files.append(p)

    for py_file in sorted(set(candidate_files)):
        mod = _safe_import_file(py_file, root)
        if mod is None:
            continue

        for attr_name, val in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(val, ModelPrototype)
                and val is not ModelPrototype
                and not inspect.isabstract(val)
                and getattr(val, "__module__", "") == mod.__name__
            ):
                setattr(val, "cls", val)
                setattr(val, "name", val.__name__)
                setattr(val, "source_file", py_file.resolve())
                setattr(val, "source_stem", py_file.stem)
                setattr(val, "_source_file", py_file.resolve())
                setattr(val, "_source_stem", py_file.stem)
                discovered[val.__name__] = val

    return discovered


def discover_configs(
    configs_dir: Path | str | None = None,
    project_root: Path | None = None,
) -> list[Path]:
    """Discover all .toml configuration preset files."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    settings = load_project_settings(root)

    cfg_dirs: list[Path] = []
    if configs_dir:
        c_path = Path(configs_dir)
        cfg_dirs.append(c_path if c_path.is_absolute() else (root / c_path).resolve())
    elif "configs" in settings:
        for c in settings["configs"]:
            cfg_dirs.append((root / c).resolve())
    else:
        if (root / "configs").is_dir():
            cfg_dirs.append(root / "configs")
        else:
            cfg_dirs.append(root)

    found: list[Path] = []
    ignored_names = {"pyproject.toml"}
    for d in cfg_dirs:
        if not d.exists():
            continue
        if d.is_file() and d.suffix == ".toml" and d.name not in ignored_names:
            found.append(d)
        elif d.is_dir():
            for p in d.rglob("*.toml"):
                if p.name in ignored_names or p.name.startswith("."):
                    continue
                found.append(p)

    return sorted(set(found))


def discover_variants(
    models: dict[str, Any] | None = None,
    configs: list[Path] | None = None,
    dist_dir: Path | str = "dist",
    project_root: Path | None = None,
    models_dir: Path | str | None = None,
    configs_dir: Path | str | None = None,
) -> list[VariantSpec]:
    """Pair discovered models with configuration presets to produce VariantSpecs."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    search_dirs = [Path(models_dir)] if models_dir else None
    all_models = models if models is not None else discover_models(search_dirs=search_dirs, project_root=root)
    all_configs = configs if configs is not None else discover_configs(configs_dir=configs_dir, project_root=root)
    out_base = Path(dist_dir)
    if not out_base.is_absolute():
        out_base = root / out_base

    if not all_models:
        return []

    # Normalize models dictionary to type[ModelPrototype]
    normalized_models: dict[str, type[ModelPrototype]] = {}
    for k, v in all_models.items():
        if isinstance(v, DiscoveredModel):
            normalized_models[k] = v.cls
        else:
            normalized_models[k] = v

    variants: list[VariantSpec] = []

    # Case 1: No configuration files found -> synthesize default variant per model
    if not all_configs:
        for m_name, model_cls in normalized_models.items():
            var_name = "default" if len(normalized_models) == 1 else f"{to_snake_case(m_name)}_default"
            src_file = getattr(model_cls, "_source_file", root / "models" / f"{to_snake_case(m_name)}.py")
            variants.append(
                VariantSpec(
                    name=var_name,
                    model_cls=model_cls,
                    config_path=None,
                    output_dir=out_base / var_name,
                    source_file=Path(src_file),
                )
            )
        return variants

    # Case 2: Config files present
    for cfg_path in all_configs:
        var_name = cfg_path.stem

        # Try to resolve target model
        target_model: type[ModelPrototype] | None = None
        try:
            raw_data = read_config_file(cfg_path, strip_directives=False)
            model_directive = raw_data.get("model")
            if model_directive and str(model_directive) in normalized_models:
                target_model = normalized_models[str(model_directive)]
        except Exception:
            pass

        if target_model is None and len(normalized_models) == 1:
            target_model = next(iter(normalized_models.values()))

        if target_model is None:
            # Match by directory slug e.g. configs/<model_slug>/<variant>.toml
            parent_name = cfg_path.parent.name.lower()
            for m_name, m_cls in normalized_models.items():
                if parent_name in (m_name.lower(), to_snake_case(m_name)):
                    target_model = m_cls
                    break

        if target_model is None:
            # Match by stem
            for m_name, m_cls in normalized_models.items():
                if var_name.lower() in (m_name.lower(), to_snake_case(m_name)):
                    target_model = m_cls
                    break

        if target_model is None:
            # Fallback to single match if only one candidate
            target_model = next(iter(normalized_models.values()))

        src_file = getattr(target_model, "_source_file", root / "models" / f"{to_snake_case(target_model.__name__)}.py")
        variants.append(
            VariantSpec(
                name=var_name,
                model_cls=target_model,
                config_path=cfg_path,
                output_dir=out_base / var_name,
                source_file=Path(src_file),
            )
        )

    return variants


# ==============================================================================
# Build Execution Engine
# ==============================================================================

def filter_variants(
    variants: Sequence[VariantSpec],
    targets: Sequence[str] | None = None,
) -> list[VariantSpec]:
    """Filter variants against target names or raise ValueError on unmatched targets."""
    if not targets:
        return list(variants)

    matched: list[VariantSpec] = []
    unmatched: list[str] = []

    for target in targets:
        target_clean = target.removesuffix(".toml")
        found = False
        for v in variants:
            if (
                v.name == target
                or v.name == target_clean
                or v.model_cls.__name__ == target
                or (v.config_path and (v.config_path.name == target or str(v.config_path) == target))
            ):
                if v not in matched:
                    matched.append(v)
                found = True
        if not found:
            unmatched.append(target)

    if unmatched:
        raise ValueError(f"Unknown target(s): {', '.join(unmatched)}")

    return matched


def build_variant(
    variant: VariantSpec,
    dist_dir: Path,
    project_root: Path,
    force: bool = False,
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.05,
    epoch: int | None = None,
) -> BuildResult:
    """Build a single variant, export artifacts, compute manifest, and check cache."""

    t0 = time.time()
    variant_name = variant.name
    model_cls = variant.model_cls
    config_path = variant.config_path
    output_dir = variant.output_dir
    source_file = variant.source_file

    # 1. Instantiate model with resolved config parameters
    try:
        if config_path:
            model_instance = load_config_file(config_path, model_cls, configs_dir=config_path.parent)
        else:
            model_instance = model_cls()
    except Exception as exc:
        return BuildResult(
            variant=variant_name,
            model=model_cls.__name__,
            output_dir=str(output_dir),
            success=False,
            error=f"Configuration validation failed: {exc}",
            elapsed_time=time.time() - t0,
        )

    # 2. Fast Cache Verification (< 5ms)
    manifest_path = output_dir / "manifest.json"
    if not force and manifest_path.is_file():
        tentative = compute_manifest(
            model_instance=model_instance,
            source_file=source_file,
            variant_name=variant_name,
            project_root=project_root,
        )
        if is_cache_valid(manifest_path, tentative):
            return BuildResult(
                variant=variant_name,
                model=model_cls.__name__,
                output_dir=str(output_dir),
                cached=True,
                success=True,
                elapsed_time=time.time() - t0,
            )

    # 3. Full Build Pipeline
    try:
        built_shape = model_instance.build()
    except Exception as exc:
        return BuildResult(
            variant=variant_name,
            model=model_cls.__name__,
            output_dir=str(output_dir),
            success=False,
            error=f"Model build() execution failed: {exc}",
            elapsed_time=time.time() - t0,
        )

    # 4. B-Rep Verification
    report = model_instance.verify(built_shape)
    metrics = {
        "bounding_box_mm": list(report.bounding_box_mm),
        "volume_mm3": report.volume_mm3,
        "solids": report.solids,
        "faces": report.faces,
        "watertight": report.watertight,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    model_slug = getattr(built_shape, "label", None) or getattr(model_cls, "_source_stem", None) or to_snake_case(model_cls.__name__)

    # 5. Deterministic STEP Export
    try:
        if isinstance(built_shape, dict):
            combined = Compound(children=list(built_shape.values()))
            export_deterministic_step(combined, output_dir / f"{model_slug}.step", epoch=epoch)
        else:
            export_deterministic_step(built_shape, output_dir / f"{model_slug}.step", epoch=epoch)
    except Exception as exc:
        return BuildResult(
            variant=variant_name,
            model=model_cls.__name__,
            output_dir=str(output_dir),
            success=False,
            error=f"STEP export failed: {exc}",
            elapsed_time=time.time() - t0,
        )

    # 6. Canonical Binary STL Export
    try:
        if isinstance(built_shape, dict):
            for part_key, part_shape in built_shape.items():
                export_canonical_stl(
                    part_shape,
                    output_dir / f"{model_slug}_{part_key}.stl",
                    model_name=f"{model_cls.__name__}_{part_key}",
                    config_name=variant_name,
                    linear_deflection=linear_deflection,
                    angular_deflection=angular_deflection,
                )
        else:
            export_canonical_stl(
                built_shape,
                output_dir / f"{model_slug}.stl",
                model_name=model_cls.__name__,
                config_name=variant_name,
                linear_deflection=linear_deflection,
                angular_deflection=angular_deflection,
            )
    except Exception as exc:
        return BuildResult(
            variant=variant_name,
            model=model_cls.__name__,
            output_dir=str(output_dir),
            success=False,
            error=f"STL export failed: {exc}",
            elapsed_time=time.time() - t0,
        )

    # 7. Print-Oriented STL Export (if defined)
    try:
        export_print_stl(
            model_instance,
            output_dir / f"{model_slug}_print.stl",
            model_name=model_cls.__name__,
            config_name=variant_name,
            built_shape=built_shape,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
        )
    except Exception:
        pass

    # 8. Offscreen Preview Views Rendering
    rendered_views: list[Path] = []
    try:
        views = model_instance.views()
        rendered_views = render_views(
            shape=built_shape,
            views=views,
            output_dir=output_dir,
            model_instance=model_instance,
        )
    except Exception:
        pass

    # 9. 2x2 Composite Preview Grid
    if len(rendered_views) >= 4:
        try:
            render_preview_grid(rendered_views[:4], output_dir / "preview_grid.png")
        except Exception:
            pass

    # 10. JSON Schema Export
    try:
        schemas_dir = dist_dir / "schemas"
        schemas_dir.mkdir(parents=True, exist_ok=True)
        export_json_schema(
            model_cls,
            schemas_dir / f"{model_cls.__name__}.schema.json",
            include_directives=True,
        )
    except Exception:
        pass

    # 11. Collect Artifacts & Save Manifest
    artifacts = {
        p.name: p
        for p in output_dir.iterdir()
        if p.is_file() and p.name != "manifest.json"
    }

    manifest = compute_manifest(
        model_instance=model_instance,
        source_file=source_file,
        variant_name=variant_name,
        project_root=project_root,
        artifacts=artifacts,
        metrics=metrics,
    )
    manifest.save(output_dir / "manifest.json")

    return BuildResult(
        variant=variant_name,
        model=model_cls.__name__,
        output_dir=str(output_dir),
        cached=False,
        success=True,
        artifacts=manifest.artifacts,
        metrics=metrics,
        elapsed_time=time.time() - t0,
    )


def build_project(
    targets: Sequence[str] | None = None,
    output_dir: Path | str = "dist",
    force: bool = False,
    workers: int = 1,
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.05,
    epoch: int | None = None,
    project_root: Path | None = None,
    models_dir: Path | str | None = None,
    configs_dir: Path | str | None = None,
) -> list[BuildResult]:
    """Build all or selected variants in parallel with incremental caching."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    dist_path = Path(output_dir)
    if not dist_path.is_absolute():
        dist_path = root / dist_path

    all_variants = discover_variants(
        dist_dir=dist_path,
        project_root=root,
        models_dir=models_dir,
        configs_dir=configs_dir,
    )
    selected_variants = filter_variants(all_variants, targets)

    results: list[BuildResult] = []

    if workers <= 1 or len(selected_variants) <= 1:
        for v in selected_variants:
            res = build_variant(
                variant=v,
                dist_dir=dist_path,
                project_root=root,
                force=force,
                linear_deflection=linear_deflection,
                angular_deflection=angular_deflection,
                epoch=epoch,
            )
            results.append(res)
        return sorted(results, key=lambda r: r.variant)

    # Parallel execution with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_variant = {
            executor.submit(
                build_variant,
                v,
                dist_path,
                root,
                force,
                linear_deflection,
                angular_deflection,
                epoch,
            ): v.name
            for v in selected_variants
        }
        for future in as_completed(future_to_variant):
            v_name = future_to_variant[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                results.append(
                    BuildResult(
                        variant=v_name,
                        model="",
                        output_dir="",
                        success=False,
                        error=f"Thread worker exception: {exc}",
                    )
                )

    return sorted(results, key=lambda r: r.variant)


build_all_variants = build_project


# ==============================================================================
# B-Rep Verification & Check Engine
# ==============================================================================

def check_project(
    targets: Sequence[str] | None = None,
    strict: bool = False,
    project_root: Path | None = None,
    models_dir: Path | str | None = None,
    configs_dir: Path | str | None = None,
) -> list[CheckResult]:
    """Validate configurations and enforce B-Rep watertightness and manifold gates."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    all_variants = discover_variants(
        project_root=root,
        models_dir=models_dir,
        configs_dir=configs_dir,
    )
    selected_variants = filter_variants(all_variants, targets)

    results: list[CheckResult] = []

    for v in selected_variants:
        errors: list[str] = []
        config_valid = False
        geometry_valid = False
        model_instance: ModelPrototype | None = None
        report_dict: dict[str, Any] | None = None

        # 1. Configuration Validation Gate
        try:
            if v.config_path:
                model_instance = load_config_file(v.config_path, v.model_cls, configs_dir=v.config_path.parent)
            else:
                model_instance = v.model_cls()
            config_valid = True
        except ConfigError as cerr:
            errors.append(str(cerr))
        except Exception as exc:
            errors.append(f"Configuration error: {exc}")

        # 2. Geometry B-Rep Verification Gate
        if config_valid and model_instance is not None:
            try:
                shape = model_instance.build()
                report = model_instance.verify(shape)
                report_dict = report.model_dump()

                if not report.is_valid:
                    errors.append("B-Rep geometry validity check failed (is_valid=False)")
                if not report.is_manifold:
                    errors.append("Geometry is non-manifold (is_manifold=False)")
                if not report.watertight:
                    errors.append("Solid is not watertight")
                if report.volume_mm3 <= 0.0:
                    errors.append(f"Non-positive volume: {report.volume_mm3} mm³")
                if report.errors:
                    errors.extend(report.errors)

                if not errors:
                    geometry_valid = True
            except Exception as exc:
                errors.append(f"Model build() failed: {exc}")

        success = config_valid and geometry_valid and (len(errors) == 0)
        results.append(
            CheckResult(
                variant=v.name,
                model=v.model_cls.__name__,
                success=success,
                config_valid=config_valid,
                geometry_valid=geometry_valid,
                errors=errors,
                report=report_dict,
            )
        )

    return sorted(results, key=lambda r: r.variant)


check_all = check_project


# ==============================================================================
# Variant Listing & Clean Subroutines
# ==============================================================================

def list_project(
    dist_dir: Path | str = "dist",
    project_root: Path | None = None,
    models_dir: Path | str | None = None,
    configs_dir: Path | str | None = None,
) -> list[VariantStatus]:
    """Query and report discovered models, variants, and cache validity."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    dist_path = Path(dist_dir)
    if not dist_path.is_absolute():
        dist_path = root / dist_path

    variants = discover_variants(
        dist_dir=dist_path,
        project_root=root,
        models_dir=models_dir,
        configs_dir=configs_dir,
    )
    statuses: list[VariantStatus] = []

    for v in variants:
        manifest_path = v.output_dir / "manifest.json"
        is_up_to_date = False

        if manifest_path.is_file():
            try:
                if v.config_path:
                    inst = load_config_file(v.config_path, v.model_cls, configs_dir=v.config_path.parent)
                else:
                    inst = v.model_cls()
                tentative = compute_manifest(inst, v.source_file, variant_name=v.name, project_root=root)
                is_up_to_date = is_cache_valid(manifest_path, tentative)
            except Exception:
                is_up_to_date = False

        cfg_label = str(v.config_path.relative_to(root)) if v.config_path else "<default>"
        out_label = str(v.output_dir.relative_to(root)) if v.output_dir.is_relative_to(root) else str(v.output_dir)

        if not manifest_path.is_file():
            status = "UNBUILT"
        elif is_up_to_date:
            status = "UP-TO-DATE"
        else:
            status = "STALE"

        statuses.append(
            VariantStatus(
                model_name=v.model_cls.__name__,
                variant_name=v.name,
                config_file=cfg_label,
                output_dir=out_label,
                status=status,
            )
        )

    return statuses


list_variants = list_project


def clean_dist(dist_dir: Path | str = "dist", project_root: Path | None = None) -> bool:
    """Safely remove dist/ build artifacts. Idempotent when dist/ does not exist."""
    root = (project_root or find_project_root(Path.cwd())).resolve()
    target = Path(dist_dir)
    if not target.is_absolute():
        target = root / target

    if target.is_dir():
        shutil.rmtree(target)
        return True
    elif target.exists():
        target.unlink()
        return True
    return False
