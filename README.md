# build123d-forge

Production Python library and CLI tool providing declarative, schema-driven parametric CAD modeling, deterministic bit-for-bit exports, content-addressed caching, and automated release packaging for `build123d` models.

## Features

- **Declarative Prototypes (`ModelPrototype`)**: Pydantic v2 `BaseModel` foundation with frozen immutability, extra-field prohibition, and physical units metadata.
- **Strict JSON Schema Draft 2020-12**: Full Draft 2020-12 schema generation with `$schema` header, property bounds, descriptions, and physical units for IDE autocompletion (Taplo/VS Code).
- **build123d-mcp Compatibility Shim (`mcp_shim`)**: Zero-breakage interoperability bridge providing runtime and static AST `PART_INFO`, `make()`, `make_print_oriented()`, and builtins hooks.
- **Sparse Configurations & Typo Diagnostics**: TOML preset configurations with cycle-checked `extends` inheritance and fuzzy typo suggestions.
- **Deterministic Exports**: Normalized STEP timestamps (honoring `SOURCE_DATE_EPOCH`) and canonical binary STL generation with locked deflections.
- **Content-Addressed Caching**: AST closure hashing and manifest tracking for sub-50ms cache hits.
- **Command-Line Interface (`bforge`)**: Full suite of CLI commands for building, checking, previewing, and releasing parametric parts.

## Installation & Offline Setup

In development environments, link `src/` into your Python virtual environment using a `.pth` file:

```bash
echo "/absolute/path/to/build123d-forge/src" > <venv>/lib/python3.12/site-packages/build123d_forge.pth
```

## Running Tests

Execute offline test suites via:

```bash
python -m unittest discover -s tests -v
```
