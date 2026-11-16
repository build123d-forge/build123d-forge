"""build123d-forge: Declarative configurations & reproducible CAD releases for build123d."""

__version__ = "0.1.0"

from build123d_forge.agents import (
    get_agents_directive,
    scaffold_agents,
)
from build123d_forge.builder import (
    BuildResult,
    CheckResult,
    DiscoveredModel,
    VariantSpec,
    VariantStatus,
    build_project,
    build_variant,
    check_project,
    clean_dist,
    discover_configs,
    discover_models,
    discover_variants,
    filter_variants,
    list_project,
)
from build123d_forge.cache import (
    BuildManifest,
    clear_cache,
    compute_ast_closure_hash,
    compute_manifest,
    compute_param_hash,
    is_cache_valid,
)
from build123d_forge.cli import cli, main
from build123d_forge.config import (
    ConfigError,
    create_config_template,
    discover_config_files,
    load_config_file,
    read_config_file,
    scaffold_config,
)
from build123d_forge.exporters import (
    export_canonical_stl,
    export_deterministic_step,
    export_print_stl,
    format_step_timestamp,
    make_canonical_stl_header,
    normalize_step_file,
)
from build123d_forge.mcp_shim import (
    generate_part_info,
    generate_part_info_ast,
    generate_part_info_code,
    mcp_shim,
)
from build123d_forge.prototype import (
    ModelPrototype,
    Param,
    ValidationReport,
    ViewSpec,
)
from build123d_forge.release import (
    generate_release_notes,
    package_release,
)
from build123d_forge.render import (
    render_preview_grid,
    render_view,
    render_views,
    strip_png_chunks,
    strip_png_metadata,
)
from build123d_forge.schema import (
    export_json_schema,
    generate_json_schema,
    serialize_json_schema,
)
from build123d_forge.watcher import (
    watch_and_rebuild,
)

__all__ = [
    "__version__",
    "cli",
    "main",
    "build_project",
    "build_variant",
    "check_project",
    "clean_dist",
    "list_project",
    "discover_models",
    "discover_configs",
    "discover_variants",
    "filter_variants",
    "BuildResult",
    "CheckResult",
    "VariantSpec",
    "VariantStatus",
    "DiscoveredModel",
    "package_release",
    "generate_release_notes",
    "watch_and_rebuild",
    "BuildManifest",
    "clear_cache",
    "compute_ast_closure_hash",
    "compute_manifest",
    "compute_param_hash",
    "is_cache_valid",
    "ConfigError",
    "load_config_file",
    "read_config_file",
    "discover_config_files",
    "scaffold_config",
    "create_config_template",
    "get_agents_directive",
    "scaffold_agents",
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
    "render_view",
    "render_views",
    "render_preview_grid",
    "strip_png_chunks",
    "strip_png_metadata",
    "export_deterministic_step",
    "export_canonical_stl",
    "export_print_stl",
    "normalize_step_file",
    "make_canonical_stl_header",
    "format_step_timestamp",
]
