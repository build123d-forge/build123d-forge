"""Content-addressed incremental caching and Python AST import closure hashing.

Computes reproducible AST import closure fingerprints, canonical parameter hashes,
build manifests, and sub-50ms cache verification for build123d-forge models.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import importlib
import importlib.metadata
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Thread-safe in-memory memoization caches for incremental build evaluation
_CACHE_LOCK = threading.Lock()

# Key: (resolved_root_path, resolved_project_root)
# Value: (fingerprints, closure_hash)
# where fingerprints: tuple[tuple[Path, int, int, str], ...] -> (file_path, mtime_ns, size, content_sha)
_AST_CLOSURE_CACHE: dict[
    tuple[Path, Path],
    tuple[tuple[tuple[Path, int, int, str], ...], str],
] = {}

# Key: (module_name, imported_name, level, current_dir, project_root)
# Value: resolved_path_or_none
_IMPORT_RESOLVE_CACHE: dict[
    tuple[str | None, str | None, int, Path, Path],
    Path | None,
] = {}

# Key: resolved_artifact_path
# Value: (mtime_ns, size, sha256_hex)
_ARTIFACT_HASH_CACHE: dict[Path, tuple[int, int, str]] = {}

# Sideband tracking of unresolved / higher-priority candidate paths so newly
# created local modules immediately invalidate cached import & AST closures.
_IMPORT_PRIOR_CANDIDATES: dict[
    tuple[str | None, str | None, int, Path, Path],
    tuple[Path, ...],
] = {}
_AST_MISSING_CANDIDATES: dict[tuple[Path, Path], tuple[Path, ...]] = {}


def clear_cache() -> None:
    """Clear all in-memory AST closure, import resolution, artifact, and dependency caches."""
    with _CACHE_LOCK:
        _AST_CLOSURE_CACHE.clear()
        _AST_MISSING_CANDIDATES.clear()
        _IMPORT_RESOLVE_CACHE.clear()
        _IMPORT_PRIOR_CANDIDATES.clear()
        _ARTIFACT_HASH_CACHE.clear()
        get_dependency_versions.cache_clear()


class BuildManifest(BaseModel):
    """Pydantic model representing build manifest stored at dist/<variant>/manifest.json."""

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )

    schema_version: int = 1
    model_name: str = ""
    prototype: str = ""
    variant_name: str = ""
    config_name: str = ""
    config: str = ""
    ast_hash: str = ""
    param_hash: str = ""
    input_sha256: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    resolved_params: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: int = Field(default_factory=lambda: int(time.time()))

    def save(self, path: Path | str) -> Path:
        """Write manifest to disk as formatted JSON."""
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | str) -> BuildManifest:
        """Load manifest from disk."""
        target = Path(path).resolve()
        data = json.loads(target.read_text(encoding="utf-8"))
        return cls.model_validate(data)


Manifest = BuildManifest


def find_project_root(start_path: Path) -> Path:
    """Find the root directory of the workspace or project."""
    current = start_path.resolve()
    if current.is_file():
        current = current.parent
    root = current
    while current != current.parent:
        if (
            (current / ".git").exists()
            or (current / "pyproject.toml").exists()
            or (current / "setup.py").exists()
            or (current / "setup.cfg").exists()
        ):
            return current
        current = current.parent
    return root


class _ImportExtractor(ast.NodeVisitor):
    """Extracts all module import targets from an AST tree."""

    def __init__(self) -> None:
        self.imports: list[tuple[str | None, str | None, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, None, 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports.append((node.module, alias.name, node.level))


def _is_local_file(candidate: Path, project_root: Path, current_dir: Path) -> bool:
    """Determine whether a file is within the local workspace and outside virtualenvs/site-packages."""
    try:
        parts = candidate.parts
        if any(bad in parts for bad in (".venv", "venv", "site-packages", ".git", "dist", "__pycache__")):
            return False
        return candidate.is_relative_to(project_root) or candidate.is_relative_to(current_dir)
    except Exception:
        return False


def _resolve_module_with_candidates(
    module_name: str | None,
    imported_name: str | None,
    level: int,
    current_file: Path,
    project_root: Path,
) -> tuple[Path | None, tuple[Path, ...]]:
    """Resolve an import statement to a local Python file and return unchecked higher-priority candidates."""
    current_dir = current_file.parent
    cache_key = (module_name, imported_name, level, current_dir, project_root)
    with _CACHE_LOCK:
        if cache_key in _IMPORT_RESOLVE_CACHE:
            cached_res = _IMPORT_RESOLVE_CACHE[cache_key]
            prior = _IMPORT_PRIOR_CANDIDATES.get(cache_key, ())
            if cached_res is not None and cached_res.is_file() and not any(p.is_file() for p in prior):
                return cached_res, prior
            _IMPORT_RESOLVE_CACHE.pop(cache_key, None)
            _IMPORT_PRIOR_CANDIDATES.pop(cache_key, None)

    def _cache_and_return(result: Path | None, prior_missing: list[Path]) -> tuple[Path | None, tuple[Path, ...]]:
        prior_tuple = tuple(dict.fromkeys(prior_missing))
        if result is not None and result.is_file():
            with _CACHE_LOCK:
                _IMPORT_RESOLVE_CACHE[cache_key] = result
                _IMPORT_PRIOR_CANDIDATES[cache_key] = prior_tuple
        return result, prior_tuple

    # Relative imports (level > 0)
    if level > 0:
        base_dir = current_dir
        for _ in range(level - 1):
            base_dir = base_dir.parent

        candidates: list[Path] = []
        if module_name:
            parts = module_name.split(".")
            if imported_name:
                candidates.append(base_dir.joinpath(*parts, f"{imported_name}.py"))
                candidates.append(base_dir.joinpath(*parts, imported_name, "__init__.py"))
            candidates.append(base_dir.joinpath(*parts[:-1], f"{parts[-1]}.py"))
            candidates.append(base_dir.joinpath(*parts, "__init__.py"))
        elif imported_name:
            candidates.append(base_dir / f"{imported_name}.py")
            candidates.append(base_dir / imported_name / "__init__.py")

        prior_missing: list[Path] = []
        for cand in candidates:
            try:
                if cand.is_file():
                    resolved = cand.resolve()
                    if _is_local_file(resolved, project_root, current_dir):
                        return _cache_and_return(resolved, prior_missing)
                else:
                    prior_missing.append(cand)
            except Exception:
                pass
        return _cache_and_return(None, prior_missing)

    # Absolute imports (level == 0)
    if not module_name:
        return _cache_and_return(None, [])

    top_pkg = module_name.split(".")[0]
    # Filter standard library and built-ins
    if top_pkg in sys.stdlib_module_names or top_pkg in sys.builtin_module_names:
        return _cache_and_return(None, [])

    search_dirs = [current_dir, project_root, project_root / "src"]
    curr = current_dir
    while curr != project_root and curr != curr.parent:
        if curr not in search_dirs:
            search_dirs.append(curr)
        curr = curr.parent

    parts = module_name.split(".")
    candidates = []
    for sdir in search_dirs:
        if not sdir.is_dir():
            continue
        if imported_name:
            candidates.append(sdir.joinpath(*parts, f"{imported_name}.py"))
            candidates.append(sdir.joinpath(*parts, imported_name, "__init__.py"))
        candidates.append(sdir.joinpath(*parts[:-1], f"{parts[-1]}.py"))
        candidates.append(sdir.joinpath(*parts, "__init__.py"))
        candidates.append(sdir / f"{parts[0]}.py")
        candidates.append(sdir / parts[0] / "__init__.py")

    prior_missing = []
    for cand in candidates:
        try:
            if cand.is_file():
                resolved = cand.resolve()
                if _is_local_file(resolved, project_root, current_dir):
                    return _cache_and_return(resolved, prior_missing)
            else:
                prior_missing.append(cand)
        except Exception:
            pass
    return _cache_and_return(None, prior_missing)


def _resolve_module_path(
    module_name: str | None,
    imported_name: str | None,
    level: int,
    current_file: Path,
    project_root: Path,
) -> Path | None:
    """Resolve an import statement to a local Python file on disk, if one exists."""
    resolved, _ = _resolve_module_with_candidates(
        module_name=module_name,
        imported_name=imported_name,
        level=level,
        current_file=current_file,
        project_root=project_root,
    )
    return resolved


def compute_ast_closure_hash(
    source_file: Path | str,
    project_root: Path | str | None = None,
) -> str:
    """Traverse local workspace module imports recursively and return deterministic SHA-256 hash.

    Features:
    - Parses AST of source file and transitively discovers all imported local modules.
    - Excludes standard library and installed site-packages.
    - Protected against circular dependencies.
    - Invariant to comments, blank lines, and line ending variations (CRLF vs LF).
    - Returns a 64-character lowercase hexadecimal SHA-256 digest.
    - Sub-millisecond memoized evaluation when source files are unchanged on disk.
    """
    root_path = Path(source_file).resolve()
    if not root_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_file}")

    if project_root is None:
        p_root = find_project_root(root_path)
    else:
        p_root = Path(project_root).resolve()

    cache_key = (root_path, p_root)
    with _CACHE_LOCK:
        cached_entry = _AST_CLOSURE_CACHE.get(cache_key)
        missing_cands = _AST_MISSING_CANDIDATES.get(cache_key, ())

    if cached_entry is not None:
        fingerprints, cached_hash = cached_entry
        valid = True
        for mc in missing_cands:
            try:
                if mc.is_file():
                    valid = False
                    break
            except OSError:
                valid = False
                break
        if valid:
            for fp in fingerprints:
                fpath, mtime_ns, size, content_sha = fp
                try:
                    st = fpath.stat()
                    if st.st_mtime_ns != mtime_ns or st.st_size != size:
                        valid = False
                        break
                    if hashlib.sha256(fpath.read_bytes()).hexdigest() != content_sha:
                        valid = False
                        break
                except (OSError, FileNotFoundError):
                    valid = False
                    break
        if valid:
            return cached_hash

    visited: set[Path] = set()
    queue: list[Path] = [root_path]
    parsed_trees: dict[Path, ast.AST] = {}
    closure_missing: list[Path] = []

    while queue:
        current = queue.pop(0)
        if not current.is_file():
            continue
        if current in visited:
            continue
        visited.add(current)

        try:
            source_content = current.read_text(encoding="utf-8")
            tree = ast.parse(source_content, filename=str(current))
            parsed_trees[current] = tree
        except Exception:
            continue

        extractor = _ImportExtractor()
        extractor.visit(tree)

        for mod_name, imp_name, level in extractor.imports:
            dep_path, prior_missing = _resolve_module_with_candidates(
                module_name=mod_name,
                imported_name=imp_name,
                level=level,
                current_file=current,
                project_root=p_root,
            )
            closure_missing.extend(prior_missing)
            if dep_path is not None and dep_path not in visited:
                queue.append(dep_path)

    # Deterministic file ordering
    def sort_key(p: Path) -> str:
        try:
            return str(p.relative_to(p_root))
        except ValueError:
            return p.name

    sorted_files = sorted(visited, key=sort_key)
    hasher = hashlib.sha256()

    for fpath in sorted_files:
        if not fpath.is_file():
            continue
        rel_identifier = sort_key(fpath)
        tree = parsed_trees.get(fpath)
        if tree is not None:
            ast_repr = ast.dump(tree, include_attributes=False)
        else:
            try:
                code = fpath.read_text(encoding="utf-8")
                tree = ast.parse(code, filename=str(fpath))
                ast_repr = ast.dump(tree, include_attributes=False)
            except Exception:
                # Fallback to normalized raw code if unparseable
                try:
                    raw = fpath.read_text(encoding="utf-8", errors="replace")
                    ast_repr = "RAW:" + raw.replace("\r\n", "\n").replace("\r", "\n")
                except Exception:
                    ast_repr = "MISSING:" + rel_identifier

        entry = f"FILE:{rel_identifier}\nAST:{ast_repr}\n"
        hasher.update(entry.encode("utf-8"))

    closure_hash = hasher.hexdigest()

    fingerprints_list: list[tuple[Path, int, int, str]] = []
    for fpath in sorted_files:
        try:
            if not fpath.is_file():
                continue
            st = fpath.stat()
            content_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            fingerprints_list.append((fpath, st.st_mtime_ns, st.st_size, content_sha))
        except (OSError, FileNotFoundError):
            pass

    with _CACHE_LOCK:
        _AST_CLOSURE_CACHE[cache_key] = (tuple(fingerprints_list), closure_hash)
        _AST_MISSING_CANDIDATES[cache_key] = tuple(dict.fromkeys(closure_missing))

    return closure_hash


def canonicalize_params(obj: Any) -> Any:
    """Normalize and sort parameter values recursively for deterministic serialization."""
    if isinstance(obj, dict):
        return {str(k): canonicalize_params(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, (list, tuple)):
        return [canonicalize_params(item) for item in obj]
    elif isinstance(obj, float):
        if obj == 0.0:
            return 0.0
        if math.isnan(obj) or math.isinf(obj):
            return str(obj)
        # Round to 12 decimal places to eliminate floating-point representation jitter
        return round(obj, 12)
    elif isinstance(obj, (int, str, bool)) or obj is None:
        return obj
    elif hasattr(obj, "model_dump"):
        return canonicalize_params(obj.model_dump(mode="json"))
    elif hasattr(obj, "__dict__"):
        return canonicalize_params({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
    else:
        return str(obj)


def canonical_param_json(parameters: dict[str, Any] | BaseModel) -> str:
    """Serialize parameters to canonical JSON string with sorted keys and compact separators."""
    if hasattr(parameters, "model_dump"):
        data = parameters.model_dump(mode="json")
    elif isinstance(parameters, dict):
        data = parameters
    else:
        data = dict(parameters)

    canonical = canonicalize_params(data)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def compute_param_hash(parameters: dict[str, Any] | BaseModel) -> str:
    """Compute SHA-256 hash of canonical parameter string."""
    canon_json = canonical_param_json(parameters)
    return hashlib.sha256(canon_json.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=1)
def get_dependency_versions() -> dict[str, str]:
    """Inspect and return installed versions of core CAD and modeling dependencies."""
    deps: dict[str, str] = {
        "python": sys.version.split()[0],
    }
    for pkg in ("build123d", "pydantic", "vtk"):
        try:
            deps[pkg] = importlib.metadata.version(pkg)
        except Exception:
            try:
                mod = importlib.import_module(pkg)
                deps[pkg] = str(getattr(mod, "__version__", "unknown"))
            except Exception:
                pass

    try:
        import OCP
        deps["cadquery_ocp"] = str(getattr(OCP, "__version__", "unknown"))
    except Exception:
        pass

    return deps


def get_file_sha256(path: Path | str) -> str:
    """Compute or return memoized SHA-256 digest of a file keyed on (mtime_ns, size)."""
    fpath = Path(path).resolve()
    st = fpath.stat()
    with _CACHE_LOCK:
        cached = _ARTIFACT_HASH_CACHE.get(fpath)
        if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]

    hasher = hashlib.sha256()
    with open(fpath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest()

    with _CACHE_LOCK:
        _ARTIFACT_HASH_CACHE[fpath] = (st.st_mtime_ns, st.st_size, digest)
    return digest


def compute_manifest(
    model_instance: Any,
    source_file: Path | str,
    variant_name: str = "default",
    project_root: Path | str | None = None,
    artifacts: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> BuildManifest:
    """Generate a BuildManifest data model capturing input closure, parameters, and artifacts."""
    src_path = Path(source_file).resolve()
    if project_root is None:
        p_root = find_project_root(src_path)
    else:
        p_root = Path(project_root).resolve()

    model_cls = getattr(model_instance, "__class__", type(model_instance))
    model_name = getattr(model_cls, "__name__", str(model_cls))
    mod_name = getattr(model_cls, "__module__", "")
    prototype = f"{mod_name}:{model_name}" if mod_name else model_name

    # Extract resolved parameters
    if hasattr(model_instance, "model_dump"):
        raw_params = model_instance.model_dump(mode="json")
    elif isinstance(model_instance, dict):
        raw_params = dict(model_instance)
    elif hasattr(model_instance, "__dict__"):
        raw_params = {k: v for k, v in model_instance.__dict__.items() if not k.startswith("_")}
    else:
        raw_params = {}

    param_hash = compute_param_hash(raw_params)
    ast_hash = compute_ast_closure_hash(src_path, project_root=p_root)
    dependencies = get_dependency_versions()

    # Calculate input_sha256 closure hash
    dep_str = ",".join(f"{k}={v}" for k, v in sorted(dependencies.items()))
    composite_input = f"{ast_hash}:{param_hash}:{dep_str}"
    input_sha256 = hashlib.sha256(composite_input.encode("utf-8")).hexdigest()

    # Process artifacts
    artifacts_dict: dict[str, Any] = {}
    if artifacts:
        for art_name, art_val in artifacts.items():
            if isinstance(art_val, Path) or (isinstance(art_val, str) and Path(art_val).is_file()):
                fpath = Path(art_val)
                if fpath.is_file():
                    artifacts_dict[art_name] = get_file_sha256(fpath)
                else:
                    artifacts_dict[art_name] = ""
            elif isinstance(art_val, dict):
                artifacts_dict[art_name] = art_val.get("sha256", "")
            elif isinstance(art_val, str):
                artifacts_dict[art_name] = art_val
            else:
                artifacts_dict[art_name] = str(art_val)

    metrics_dict = metrics if isinstance(metrics, dict) else {}

    return BuildManifest(
        schema_version=1,
        model_name=model_name,
        prototype=prototype,
        variant_name=variant_name,
        config_name=variant_name,
        config=variant_name,
        ast_hash=ast_hash,
        param_hash=param_hash,
        input_sha256=input_sha256,
        parameters=raw_params,
        resolved_params=raw_params,
        dependencies=dependencies,
        environment=dependencies,
        artifacts=artifacts_dict,
        metrics=metrics_dict,
        created_at=int(time.time()),
    )


def is_cache_valid(
    manifest_path: Path | str,
    current_manifest: BuildManifest | None = None,
) -> bool:
    """Validate cache integrity in under 50ms without invoking OpenCASCADE or VTK.

    Checks:
    1. manifest_path exists and parses as valid JSON.
    2. If current_manifest provided:
       - schema_version matches.
       - input_sha256 / ast_hash / param_hash match.
       - parameters match.
    3. All files listed in artifacts exist on disk and match their expected SHA-256 digests.
    """
    m_path = Path(manifest_path).resolve()
    if not m_path.is_file():
        return False

    try:
        raw_text = m_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    # Check against current_manifest if provided
    if current_manifest is not None:
        if data.get("schema_version") != current_manifest.schema_version:
            return False

        if current_manifest.input_sha256 and data.get("input_sha256") != current_manifest.input_sha256:
            return False

        if current_manifest.ast_hash and data.get("ast_hash") != current_manifest.ast_hash:
            return False

        if current_manifest.param_hash and data.get("param_hash") != current_manifest.param_hash:
            return False

        curr_p = current_manifest.resolved_params or current_manifest.parameters
        disk_p = data.get("resolved_params") or data.get("parameters")
        if curr_p and disk_p:
            if canonicalize_params(curr_p) != canonicalize_params(disk_p):
                return False

    # Validate artifacts on disk
    artifacts = data.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, dict):
            return False

        base_dir = m_path.parent
        for fname, expected in artifacts.items():
            cand = base_dir / fname
            if not cand.is_file():
                cand = Path(fname)
            if not cand.is_file():
                return False

            if isinstance(expected, str):
                expected_sha = expected
            elif isinstance(expected, dict):
                expected_sha = expected.get("sha256")
            else:
                expected_sha = None

            if expected_sha:
                try:
                    actual_sha = get_file_sha256(cand)
                    if actual_sha != expected_sha:
                        return False
                except Exception:
                    return False

    return True


__all__ = [
    "BuildManifest",
    "Manifest",
    "clear_cache",
    "get_file_sha256",
    "find_project_root",
    "compute_ast_closure_hash",
    "canonicalize_params",
    "canonical_param_json",
    "compute_param_hash",
    "get_dependency_versions",
    "compute_manifest",
    "is_cache_valid",
]
