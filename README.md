# build123d-forge

Declarative, schema-driven parametric CAD modeling, bit-for-bit reproducible exports, content-addressed caching, and automated GitHub release packaging for [`build123d`](https://github.com/gumyr/build123d) models.

## Features

- **Declarative Prototypes (`ModelPrototype`)**: Subclasses Pydantic v2 `BaseModel` with `frozen=True`, `extra="forbid"`, `Param(..., unit="mm")` physical unit metadata, and `@model_validator` cross-parameter invariants.
- **Draft 2020-12 JSON Schema (`bforge schema`)**: Exports JSON Schemas (`dist/schemas/<Model>.schema.json`) with `$schema`, bounds, descriptions, and physical units for Taplo / VS Code `.toml` autocomplete.
- **Sparse Configurations & Typo Diagnostics**: Sparse `.toml` presets in `configs/` with cycle-checked `extends` inheritance and `difflib` fuzzy typo suggestions.
- **Bit-for-Bit Deterministic CAD & Previews**: Normalized `.step` headers (`SOURCE_DATE_EPOCH`), canonical 80-byte binary `.stl` & `*_print.stl` meshes with locked deflections, and headless offscreen VTK `.png` renders (`preview_grid.png`) with stripped metadata chunks.
- **Sub-5 ms Content-Addressed Caching**: Tracks transitive Python AST import closures, canonical parameter JSON digests, and dependency versions in `dist/<variant>/manifest.json`.
- **CLI & Reusable GitHub Release Workflow (`bforge`)**: `build`, `check`, `new-config`, `schema`, `list`, `clean`, `watch`, and `release-package`, paired with `.github/workflows/build-and-release.yml` for tag-triggered releases.

## Installation

Install with `uv` or `pip` (Python `>= 3.11`):

```bash
# Standard installation
uv pip install build123d-forge
# or with optional SVG fallback renderer and pytest dev dependencies in a local clone:
uv pip install -e ".[render,dev]"
```

## Quickstart

### 1. Declare a `ModelPrototype` (`models/bracket.py`)

```python
from build123d import Box, Part
from build123d_forge import ModelPrototype, Param


class Bracket(ModelPrototype):
    """Simple parametric mounting block."""

    width: float = Param(40.0, gt=0.0, unit="mm", description="Overall width")
    thickness: float = Param(4.0, gt=0.0, unit="mm", description="Wall thickness")

    def build(self) -> Part:
        return Box(self.width, 20.0, self.thickness)
```

### 2. Create Sparse Presets (`configs/`) & Build

```bash
bforge schema
bforge new-config heavy_duty
bforge check
bforge build
```

## Running Tests

Run the test suite with `pytest`:

```bash
pytest -v
```
