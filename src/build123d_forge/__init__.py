"""build123d-forge: Declarative configurations & reproducible CAD releases for build123d."""

__version__ = "0.1.0"

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
from build123d_forge.schema import (
    export_json_schema,
    export_schema,
    generate_json_schema,
    serialize_json_schema,
)

__all__ = [
    "__version__",
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
    "export_schema",
]
