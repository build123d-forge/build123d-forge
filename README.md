# build123d-forge

[![CI](https://github.com/build123d-forge/build123d-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/build123d-forge/build123d-forge/actions/workflows/ci.yml)
[![Build & Release Workflow](https://github.com/build123d-forge/build123d-forge/actions/workflows/build-and-release.yml/badge.svg)](https://github.com/build123d-forge/build123d-forge/actions/workflows/build-and-release.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![build123d >= 0.13](https://img.shields.io/badge/build123d-%3E%3D0.13-008080)](https://github.com/gumyr/build123d)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/latest/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/build123d-forge/build123d-forge/blob/main/LICENSE)

**Declarative, schema-driven parametric CAD configurations, bit-for-bit reproducible exports, content-addressed caching, and automated GitHub Releases for [`build123d`](https://github.com/gumyr/build123d) models.**

---

## Table of Contents

- **User Guide**
  - [Why `build123d-forge`?](#why-build123d-forge)
  - [End-to-End Workflow](#end-to-end-workflow)
  - [Installation & Consumer Project Structure](#installation--consumer-project-structure)
  - [User Guide & Examples](#user-guide--examples)
    - [1. Declaring a `ModelPrototype`](#1-declaring-a-modelprototype)
    - [2. Exporting Draft 2020-12 JSON Schemas & IDE Autocomplete](#2-exporting-draft-2020-12-json-schemas--ide-autocomplete)
    - [3. Sparse `.toml` Configurations, Inheritance & Typo Diagnostics](#3-sparse-toml-configurations-inheritance--typo-diagnostics)
    - [4. Interactive Authoring in `build123d-mcp` with `mcp_shim`](#4-interactive-authoring-in-build123d-mcp-with-mcp_shim)
    - [5. Delegating Builds & Previews in AI Agent Workflows (`AGENTS.md`)](#5-delegating-builds--previews-in-ai-agent-workflows-agentsmd)
  - [`bforge` CLI Reference](#bforge-cli-reference)
  - [Automated GitHub Releases & Embedding Release Previews](#automated-github-releases--embedding-release-previews)
- **Developer & Architecture Guide**
  - [Architecture Overview](#architecture-overview)
  - [Internals Deep Dive](#internals-deep-dive)
  - [Development Setup](#development-setup)
  - [Testing Architecture & Writing Future Tests](#testing-architecture--writing-future-tests)

---

## Why `build123d-forge`?

Sharing parametric 3D-printable hardware in Git usually forces an uncomfortable trade-off:

- **Committing binary `.stl`, `.step`, and `.png` files** bloats repository history by megabytes on every parameter tweak, produces opaque diffs in pull requests, and silently drifts out of sync when someone edits Python geometry without re-exporting every preset.
- **Committing only procedural `.py` scripts** keeps Git clean, but forces casual makers who just want to slice a part to install Python, OpenCASCADE, and `build123d`—while scattering magic numbers without machine-readable parameter bounds, physical units, or print-bed orientations.

`build123d-forge` treats parametric CAD like modern software release engineering:

| Capability | Traditional CAD-in-Git | `build123d-forge` |
| :--- | :--- | :--- |
| **Git Repository Contents** | Binary `.stl` / `.step` / `.png` churn in commits | **100% text-only**: `models/*.py` + sparse `configs/*.toml` |
| **Parameter Validation** | Ad-hoc globals or runtime OpenCASCADE crashes | **Pydantic v2 `ModelPrototype`** (`frozen=True`, `extra="forbid"`, `Param(..., unit="mm")`, `@model_validator` invariants) |
| **Preset Authoring** | Copy-pasted scripts per size variant | **Sparse `.toml` presets** with cycle-checked `extends` inheritance, Taplo/VS Code JSON Schema autocomplete, and fuzzy typo hints |
| **Geometry QA** | Manual visual inspection | **Automated B-Rep gates** (`bforge check`) verifying solid validity, manifoldness, watertightness, and positive volume |
| **Build Determinism & Speed** | Non-deterministic STEP timestamps & full rebuilds | **Bit-for-bit reproducible** `.step`, `.stl`, `*_print.stl`, and `.png` outputs with **sub-5 ms AST + parameter SHA-256 caching** |
| **End-User Distribution** | Manual local exports uploaded by hand | **Tag-driven GitHub Releases** (`v*`) publishing `.stl`, `.step`, `.png`, `.zip`, `RELEASE_NOTES.md`, and `SHA256SUMS.txt`—powering live `README.md` previews |

> **Live Reference Project:** See [`build123d-forge/alpstuga-shower-mount`](https://github.com/build123d-forge/alpstuga-shower-mount) for a complete production hardware repository built and published with `build123d-forge`.

---

## End-to-End Workflow

```mermaid
flowchart TD
    subgraph GitRepo["1. Git Repository (100% Text-Only)"]
        Models["models/*.py (ModelPrototype)"]
        Configs["configs/*.toml (Sparse Presets)"]
        PyProject["pyproject.toml + uv.lock"]
    end

    subgraph LocalDev["2. Local Authoring & bforge CLI"]
        Schema["bforge schema → dist/schemas/*.schema.json"]
        IDE["Taplo / VS Code TOML Autocomplete"]
        Check["bforge check (Pydantic + B-Rep Gates)"]
        Build["bforge build (AST + Param SHA-256 Cache)"]
        Dist["dist/{variant}/ (.step, .stl, *_print.stl, *.png, manifest.json)"]
    end

    subgraph ReleaseCI["3. Tag-Driven GitHub Release CI"]
        GitTag["git tag v1.0.0 + git push --tags"]
        Workflow["build-and-release.yml (Headless VTK + SOURCE_DATE_EPOCH)"]
        Pkg["bforge release-package --tag v1.0.0"]
        GHRelease["GitHub Release ({variant}.zip, {variant}__*.stl/.step/.png, SHA256SUMS.txt)"]
        Readme["Consumer README.md (/releases/latest/download/{variant}__*.png)"]
    end

    Models --> Schema
    Schema --> IDE
    IDE -.-> Configs
    Models --> Check
    Configs --> Check
    PyProject --> Check
    Check --> Build
    Build --> Dist
    Models --> GitTag
    Configs --> GitTag
    GitTag --> Workflow
    Workflow --> Pkg
    Pkg --> GHRelease
    GHRelease --> Readme
```

---

## Installation & Consumer Project Structure

### Installing `build123d-forge`

`build123d-forge` requires **Python `>= 3.11`** and installs both the `bforge` and `build123d-forge` CLI entry points. Optional extras include `[render]` (`resvg-py>=0.5.0` headless SVG fallback) and `[mcp]` (`build123d-mcp>=0.3.0` interactive MCP CAD server).

```bash
# Using uv (recommended) — from PyPI, into pyproject.toml, or from GitHub main
uv pip install "build123d-forge[render]"
uv add "build123d-forge[render]"
uv pip install "build123d-forge[render,mcp] @ git+https://github.com/build123d-forge/build123d-forge.git"

# Or using pip
pip install "build123d-forge[render]"
```

### Consumer `pyproject.toml` Configuration

Configure your parametric hardware repository with a standard `pyproject.toml` and optional `[tool.build123d_forge]` search paths:

```toml
[project]
name = "alpstuga-shower-mount"
version = "0.2.0"
description = "Symmetric parametric over-the-glass shower mount for the IKEA ALPSTUGA sensor"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "build123d>=0.13.0",
    "build123d-forge[render]>=0.1.0",
]

[tool.build123d_forge]
models = ["models"]
configs = ["configs"]
```

- **`models`**: Directories (or `.py` file paths) scanned recursively for concrete `ModelPrototype` subclasses (auto-discovering `models/`, `parts/`, or the project root when omitted, excluding `tests/`, `test_*.py`, `*_test.py`, `.venv/`, and `dist/`). Plain Python helper modules (such as mating COTS reference geometry) are tracked by the AST import cache without becoming standalone build targets unless they subclass `ModelPrototype`.
- **`configs`**: Directories (or `.toml` file paths) scanned for configuration presets. If omitted, `bforge` auto-discovers `configs/` or the project root (excluding `pyproject.toml`). If a project has no `.toml` files yet, `bforge` synthesizes a `default` variant directly from the prototype's parameter defaults.

### Recommended Consumer Project Layout

```text
alpstuga-shower-mount/
├── AGENTS.md                       # Directs AI agents to delegate builds & previews to bforge
├── pyproject.toml                  # Dependencies & [tool.build123d_forge] layout
├── uv.lock                         # Locked dependencies for reproducible CI builds
├── README.md                       # Links /releases/latest/download/<variant>__*
├── .gitignore                      # Ignores dist/, release_assets/, .venv/
├── .github/
│   └── workflows/
│       └── release.yml             # Calls reusable build-and-release.yml@main
├── models/                         # Declarative ModelPrototype sources (committed)
│   └── alpstuga_shower_mount.py
├── configs/                        # Sparse .toml parameter presets (committed)
│   ├── default.toml                # 3/8" glass baseline -> dist/default/
│   └── half_inch_glass.toml        # extends = "default.toml" -> dist/half_inch_glass/
├── resources/                      # Optional reference 3D scans, datasheets, mating STEP parts
│   └── alpstuga.step
└── dist/                           # Generated locally by `bforge` (git-ignored)
    ├── schemas/
    │   └── AlpstugaShowerMount.schema.json
    ├── default/
    │   ├── alpstuga_shower_mount.step
    │   ├── alpstuga_shower_mount.stl
    │   ├── alpstuga_shower_mount_print.stl
    │   ├── iso_shower.png, iso_rear_clip.png, bottom_airflow.png, print_bed.png
    │   ├── preview_grid.png
    │   └── manifest.json
    └── half_inch_glass/
        └── ...
```

Add generated build and release directories to your `.gitignore`:

```gitignore
dist/
release_assets/
.venv/
__pycache__/
```

---

## User Guide & Examples

### 1. Declaring a `ModelPrototype`

Every parametric part or assembly subclasses `ModelPrototype`—an immutable (`frozen=True`), strict (`extra="forbid"`, `validate_default=True`) Pydantic v2 `BaseModel` tailored for `build123d`.

```python
# models/glass_mount.py
"""Parametric over-the-glass sensor mount."""

from typing import Literal, Self

from build123d import Align, Axis, Box, BuildPart, Locations, Mode, Part, Plane, mirror
from pydantic import model_validator

from build123d_forge import ModelPrototype, Param, ViewSpec, mcp_shim


class GlassMount(ModelPrototype):
    """Parametric U-channel glass mount with configurable slot gap and handedness."""

    # Optional domain synonyms for typo diagnostics in TOML presets
    __synonyms__ = {"glass_thickness": "wall_gap", "slot_width": "wall_gap"}

    wall_gap: float = Param(
        9.625,
        gt=0.0,
        unit="mm",
        description="Glass wall slot gap in mm (3/8 in = 9.525 mm + 0.1 mm clearance)",
    )
    visor_drop: float = Param(
        10.0,
        gt=0.0,
        unit="mm",
        description="Vertical engagement depth over the top edge of the glass",
    )
    wall_thickness: float = Param(
        3.0,
        ge=1.2,
        unit="mm",
        description="Structural wall and roof thickness",
    )
    mount_width: float = Param(
        60.0,
        gt=10.0,
        unit="mm",
        description="Overall width along the glass pane",
    )
    handedness: Literal["left", "right"] = Param(
        "left",
        description="Glass wall side when viewing from inside ('left' or 'right')",
    )

    @model_validator(mode="after")
    def _check_geometric_invariants(self) -> Self:
        """Reject unbuildable or fragile parameter combinations before CAD execution."""
        if self.visor_drop < 2.0 * self.wall_thickness:
            raise ValueError(
                f"visor_drop ({self.visor_drop} mm) must be at least 2x "
                f"wall_thickness ({self.wall_thickness} mm) for clip strength."
            )
        return self

    def build(self) -> Part:
        """Construct and return the upright functional B-Rep Part solid."""
        outer_x = self.wall_gap + 2.0 * self.wall_thickness
        outer_z = self.visor_drop + self.wall_thickness

        with BuildPart() as bp:
            Box(
                outer_x,
                self.mount_width,
                outer_z,
                align=(Align.MIN, Align.CENTER, Align.MIN),
            )
            with Locations((self.wall_thickness, 0.0, 0.0)):
                Box(
                    self.wall_gap,
                    self.mount_width + 1.0,
                    self.visor_drop,
                    align=(Align.MIN, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )

        part = bp.part
        if self.handedness == "right":
            part = mirror(part, about=Plane.XZ)
        part.label = "glass_mount"
        return part

    def orient_for_print(self, part: Part | None = None) -> Part:
        """Flip upside-down onto the flat top roof (Z = 0) for zero-support FDM printing."""
        if part is None:
            part = self.build()
        total_z = self.visor_drop + self.wall_thickness
        oriented = part.rotate(Axis.X, 180.0).translate((0.0, 0.0, total_z))
        oriented.label = "glass_mount_print_oriented"
        return oriented

    def views(self) -> list[ViewSpec]:
        """Camera suite rendered to PNGs and composited into preview_grid.png."""
        return [
            ViewSpec(
                stem="iso_front",
                description="Isometric front view",
                direction="front",
                azimuth=35.0,
                elevation=25.0,
            ),
            ViewSpec(
                stem="iso_rear",
                description="Isometric rear view",
                direction="front",
                azimuth=145.0,
                elevation=25.0,
            ),
            ViewSpec(
                stem="side_slot",
                description="Glass slot profile view",
                direction="side",
                azimuth=0.0,
                elevation=0.0,
            ),
            ViewSpec(
                stem="print_bed",
                description="Inverted zero-support print bed orientation",
                direction="iso",
                azimuth=35.0,
                elevation=30.0,
                use_print_oriented=True,
            ),
        ]


GlassMount.model_rebuild()
mcp_shim(GlassMount, globals())
```

#### Key `ModelPrototype` Hooks & Primitives

- **`Param(default=..., *, unit=None, description=None, gt=None, ge=None, lt=None, le=None, ...)`**: Thin wrapper around `pydantic.Field` that attaches physical units (`json_schema_extra={"unit": unit}`) alongside numeric constraints and descriptions—propagating them into JSON Schemas, scaffolded `.toml` comments, and `PART_INFO` dictionaries.
- **`@model_validator(mode="after")`**: Standard Pydantic v2 validator for cross-parameter geometric rules (e.g., ensuring clearances, minimum wall ratios, or hole-to-edge margins before OpenCASCADE runs).
- **`build(self) -> Part | dict[str, Part]`** *(required)*:
  - Returning a single `Part` exports `<slug>.step` and `<slug>.stl` (where `<slug>` uses `part.label` if set, otherwise the Python source stem or `snake_case` class name).
  - Returning a `dict[str, Part]` (for multi-part assemblies) exports a combined `<slug>.step` `Compound` plus individual `<slug>_<part_key>.stl` meshes.
- **`orient_for_print(self, part: Part | None = None) -> Part | dict[str, Part] | None`** *(optional)*: Transforms the built geometry into its optimal slicer orientation at `Z = 0` and triggers `<slug>_print.stl` generation. Returning `None` (the default) skips `*_print.stl` export.
- **`views(self) -> list[ViewSpec]`** *(optional)*: Defines offscreen Phong-shaded VTK renders (`1000 × 750` px, stripped metadata chunks). Defaults to a 4-view suite (`iso`, `top`, `side`, and print-oriented `bed`). When 4 or more `ViewSpec` entries are returned, `bforge build` also composites the first 4 into a `2 × 2` `preview_grid.png`.
  - `ViewSpec` fields: `stem` (output `<stem>.png` filename), `description`, `direction` (`"front"`, `"iso"`, `"top"`, `"bottom"`, `"side"`/`"right"`, `"left"`, `"back"`/`"rear"`), `azimuth` (degrees), `elevation` (degrees), and `use_print_oriented` (`bool`, renders the `orient_for_print()` solid when `True`).
  - Camera pose convention: `(azimuth=0, elevation=0)` looks from `-Y` toward `+Y` with `+Z` up (`+X` right). `direction` sets a preset angle **only** when `azimuth == 0` or `elevation == 0`; non-zero `azimuth`/`elevation` values are **absolute** spherical angles in degrees (pass explicit angles such as `azimuth=35, elevation=25` with `"iso"`, or `azimuth=145, elevation=25` for rear-isometric views).
- **`verify(self, shape)` & `from_toml(path)`**: Built-in B-Rep verification gate (returning a `ValidationReport` with `is_valid`, `is_manifold`, `watertight`, `solids`, `faces`, `volume_mm3`, and `bounding_box_mm`) and programmatic TOML loader (`mount = GlassMount.from_toml("configs/default.toml")`).

---

### 2. Exporting Draft 2020-12 JSON Schemas & IDE Autocomplete

Generate Draft 2020-12 JSON Schemas (`https://json-schema.org/draft/2020-12/schema`) for every `ModelPrototype` in your project:

```bash
bforge schema
# Exported schema: /path/to/project/dist/schemas/GlassMount.schema.json
```

*(Note: `bforge build` also keeps `dist/schemas/<Model>.schema.json` updated automatically.)*

Each generated schema enforces `additionalProperties: false` while exposing parameter types, `default` values, numeric bounds (`exclusiveMinimum`, `minimum`, `maximum`), `Literal` enum choices, physical `"unit"` annotations, and the `extends` / `model` loader directives.

#### Enabling Taplo / VS Code Autocomplete

1. Install **[Even Better TOML](https://marketplace.visualstudio.com/items?itemName=tamasfe.even-better-toml)** in VS Code (or configure the `taplo` language server in Neovim, Zed, or JetBrains IDEs).
2. Place the `#:schema` directive on line 1 of your `.toml` preset (`bforge new-config` adds this header automatically):

```toml
#:schema ../dist/schemas/GlassMount.schema.json

wall_gap = 9.625
handedness = "left"
```

With the `#:schema` directive active, your editor provides:
- **Key completion (`Ctrl+Space`)** for every `ModelPrototype` parameter and loader directive (`extends`, `model`).
- **Hover documentation** showing descriptions, defaults, bounds, and units.
- **Instant inline diagnostics** if you type an unknown parameter name, violate `gt = 0.0`, or pass an invalid `Literal` choice—before running a single CLI command.

---

### 3. Sparse `.toml` Configurations, Inheritance & Typo Diagnostics

#### Scaffolding a Documented Preset

Use `bforge new-config` to scaffold a fully documented preset in `configs/`:

```bash
bforge new-config default
```

This writes `configs/default.toml` with the `#:schema` header, class docstring, field descriptions, `[unit]` tags, constraint bounds, and default values.

#### Sparse Overrides & `extends` Inheritance

You never need to repeat unchanged parameters across presets. Any `.toml` file in `configs/` can omit parameters to inherit prototype defaults, or chain onto another preset via `extends`:

```toml
# configs/default.toml — 3/8" frameless shower glass baseline
#:schema ../dist/schemas/AlpstugaShowerMount.schema.json

wall_gap = 9.625
```

```toml
# configs/half_inch_glass.toml — 1/2" glass override (inherits default.toml)
#:schema ../dist/schemas/AlpstugaShowerMount.schema.json

extends = "default.toml"
wall_gap = 12.8
```

```toml
# configs/half_inch_right.toml — Right-handed 1/2" glass variation
#:schema ../dist/schemas/AlpstugaShowerMount.schema.json

extends = "half_inch_glass.toml"
handedness = "right"
```

#### Loader Directives

The configuration loader recognizes three reserved top-level directives and strips them prior to Pydantic validation:

| Directive | Example | Purpose |
| :--- | :--- | :--- |
| `#:schema <path>` *(comment)* or `$schema` | `#:schema ../dist/schemas/GlassMount.schema.json` | Binds Taplo / IDE validation to the generated Draft 2020-12 JSON Schema |
| `extends` | `extends = "default.toml"` | Recursively deep-merges parent `.toml` presets (with cycle detection) before applying local overrides |
| `model` | `model = "GlassMount"` | Explicitly selects the target `ModelPrototype` class in multi-model repositories (presets can also match by `configs/<model_slug>/` directory or filename stem) |

#### Sparse Inheritance & Override Resolution Flow

```mermaid
flowchart TD
    Base["configs/default.toml (wall_gap = 9.625)"]
    Half["configs/half_inch_glass.toml (extends = 'default.toml', wall_gap = 12.8)"]
    Right["configs/half_inch_right.toml (extends = 'half_inch_glass.toml', handedness = 'right')"]
    Merge["deep_merge() + Strip Loader Directives (cycle-checked TOML chain)"]
    Proto["AlpstugaShowerMount Prototype Defaults (visor_drop = 10.0, sensor_clearance = 0.6, wall_thickness = 3.0)"]
    Validate["Pydantic v2 Validation (frozen=True, extra='forbid', @model_validator, typo check)"]
    Resolved["Validated Instance (wall_gap = 12.8, visor_drop = 10.0, sensor_clearance = 0.6, wall_thickness = 3.0, handedness = 'right')"]

    Base -->|"inherited by"| Half
    Half -->|"inherited by"| Right
    Right -->|"recursive TOML overrides"| Merge
    Merge -->|"merged parameter dict"| Validate
    Proto -->|"fills omitted fields"| Validate
    Validate -->|"immutable prototype"| Resolved
```

#### Fuzzy Typo Diagnostics & Circular Loop Protection

Because `ModelPrototype` enforces `extra="forbid"`, misspelled parameter keys in `.toml` presets never silently fall back to defaults. Instead, `build123d-forge` checks prototype `__synonyms__`, built-in domain synonyms, case-insensitive matches, and `difflib` edit distance to pinpoint the intended field:

```text
✗ custom_glass (AlpstugaShowerMount): Validation failed
  • configs/custom_glass.toml: Extra inputs are not permitted ('wall_thicness' — did you mean 'wall_thickness'?)
  • configs/custom_glass.toml: Extra inputs are not permitted ('glass_thickness' — did you mean 'wall_gap'?)
```

Circular `extends` chains (such as `a.toml -> b.toml -> a.toml`) are caught immediately with the full file cycle path.

---

### 4. Interactive Authoring in `build123d-mcp` with `mcp_shim`

`build123d-forge` integrates seamlessly with interactive `build123d-mcp` CAD sessions so you can inspect views, cross-sections, feature measurements, and FDM printability live while keeping your declarative `ModelPrototype` as the single source of truth.

Add two lines at the bottom of your model file:

```python
from build123d_forge import ModelPrototype, Param, mcp_shim


class AlpstugaShowerMount(ModelPrototype):
    ...


AlpstugaShowerMount.model_rebuild()
mcp_shim(AlpstugaShowerMount, globals())
```

Calling `mcp_shim(MyModel, globals())` (with optional `inject_defaults=True` if you also want top-level parameter globals) automatically wires up:

- **`PART_INFO`**: A literal-safe metadata dictionary (`description`, `tags`, and `parameters` with `type`, `default`, `description`, `unit`, `gt`/`ge`/`lt`/`le`, and `choices`) compatible with `ast.literal_eval` and `build123d-mcp` library discovery. *(You can also generate static Python source code via `generate_part_info_code(MyModel)`.)*
- **`make(**kwargs) -> Shape`**: Validates keyword overrides through Pydantic and returns the built `Part` (or `Compound` for multi-part `dict[str, Part]` assemblies)—e.g., `make(wall_gap=12.8, handedness="right")`.
- **`make_print_oriented(**kwargs) -> Shape`**: Builds the model and applies `orient_for_print()`, making it trivial to run `analyze_printability()` on the exact bed orientation inside `build123d-mcp`.
- **Automatic `result` binding**: When executed inside `build123d-mcp`'s `execute_file` or `show`-enabled environment, `mcp_shim` automatically populates `result = make()` for immediate 3D viewport rendering.

---

### 5. Delegating Builds & Previews in AI Agent Workflows (`AGENTS.md`)

Without project guidance, AI coding agents often invent bespoke `generate_*.py` scripts, ad-hoc STEP/STL exporters, or throwaway `/tmp` renders—leaving you without automated builds and previews when you later tweak a `.py` or `.toml` file manually. Run [`bforge init-agents`](#bforge-init-agents) to commit the canonical [`AGENTS.md`](src/build123d_forge/templates/AGENTS.template.md) directive at your repository root:

- **Agents edit only declarative CAD & presets**: Updating `ModelPrototype` classes (`Param`, `@model_validator`, `build()`, `orient_for_print()`, `ViewSpec`) in `models/*.py` and sparse overrides in `configs/*.toml`—never writing custom export/render scripts or committing `dist/` binaries.
- **`bforge` owns validation, builds & permanent camera views**: Agents run `bforge check` + `bforge build` (or work alongside `bforge watch`), inspect `dist/<variant>/preview_grid.png` and `manifest.json`, and add missing camera angles to `ModelPrototype.views()` so manual tweaks later render those views automatically.
- **`build123d-mcp` stays strictly diagnostic**: Agents may use `mcp_shim` (`measure()`, `cross_sections()`, `compare()`, `analyze_printability()`) for interactive CAD inspection while keeping `bforge check` + `bforge build` authoritative.

---

## `bforge` CLI Reference

Both `bforge` and `build123d-forge` invoke the same CLI (`bforge --version`, `bforge --help`). All commands auto-detect the project root from `pyproject.toml` or `.git`.

| Command | Summary |
| :--- | :--- |
| [`bforge build`](#bforge-build) | Incrementally build `.step`, `.stl`, `*_print.stl`, `.png` views, schemas, and `manifest.json` into `dist/<variant>/` |
| [`bforge check`](#bforge-check) | Validate `.toml` presets with Pydantic and run OpenCASCADE B-Rep manifold/watertightness gates |
| [`bforge new-config`](#bforge-new-config) | Scaffold a documented sparse `.toml` preset pre-populated with `#:schema`, units, bounds, and defaults |
| [`bforge init-agents`](#bforge-init-agents) | Scaffold a canonical `AGENTS.md` directive delegating CAD builds and preview renders to `bforge` |
| [`bforge schema`](#bforge-schema) | Export Draft 2020-12 JSON Schema files (`dist/schemas/<Model>.schema.json`) for IDE TOML autocomplete |
| [`bforge list`](#bforge-list) | Display discovered `ModelPrototype` classes, configuration presets, and cache status (`UP-TO-DATE` / `STALE` / `UNBUILT`) |
| [`bforge watch`](#bforge-watch) | Watch `models/`, `parts/`, and `configs/` and incrementally rebuild changed presets on save |
| [`bforge clean`](#bforge-clean) | Remove generated `dist/` build artifacts, schemas, and cache manifests |
| [`bforge release-package`](#bforge-release-package) | Bundle per-variant `.zip` archives, flattened `<variant>__*` assets, `RELEASE_NOTES.md`, and `SHA256SUMS.txt` |

---

### `bforge build`

Incrementally build all or selected model configurations into `dist/<variant>/`. Unchanged variants whose transitive Python AST import closure, canonical parameters, dependency versions, and on-disk artifact SHA-256 digests match `dist/<variant>/manifest.json` short-circuit in `< 5 ms` (`[cached]`). Pass `--force` if an external non-Python input (e.g., an imported `resources/*.step` file) changes without `.py`/`.toml` edits. Because `bforge build` still writes `.step`/`.stl` on broken solids to aid debugging, pair it with `bforge check` to enforce strict B-Rep gates.

```text
bforge build [OPTIONS] [TARGETS]...
```

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `[TARGETS]...` | *(all variants)* | Variant names (`default`, `half_inch_glass`), `.toml` paths (`configs/default.toml`), or `ModelPrototype` class names |
| `-f, --force` | `False` | Force a full rebuild, bypassing `manifest.json` cache checks |
| `-j, --workers, --jobs INTEGER` | `1` | Number of parallel worker threads |
| `-o, --output-dir PATH` | `dist` | Root output directory for `<variant>/` folders and `schemas/` |
| `--linear-deflection FLOAT` | `0.01` | Maximum chordal deviation in `mm` for STL tessellation |
| `--angular-deflection FLOAT` | `0.05` | Maximum angular deviation in `rad` for STL tessellation |
| `--epoch INTEGER` | `SOURCE_DATE_EPOCH` or `0` | Unix timestamp (seconds) normalized into STEP `FILE_NAME` headers |
| `--models-dir TEXT` | *(auto)* | Override directory containing `ModelPrototype` Python sources |
| `--configs-dir TEXT` | *(auto)* | Override directory containing `.toml` configuration presets |

```bash
# Build all variants sequentially (uses cache when up-to-date)
bforge build

# Build all variants across 4 worker threads, forcing fresh exports
bforge build -j 4 --force

# Build only specific variants or model classes
bforge build half_inch_glass quarter_inch_glass
```

---

### `bforge check`

Fast verification gate for local pre-commit checks and CI pull requests. Resolves `.toml` inheritance, validates parameters and `@model_validator` invariants, builds B-Rep solids, and asserts `is_valid`, `is_manifold`, `watertight`, and `volume_mm3 > 0` without writing export files. Exits with status `1` on any failure.

```text
bforge check [OPTIONS] [TARGETS]...
```

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `[TARGETS]...` | *(all variants)* | Optional subset of variant names, `.toml` paths, or model class names |
| `--strict` | `False` | Enable strict checking mode |
| `--models-dir TEXT` | *(auto)* | Override directory containing `ModelPrototype` Python sources |
| `--configs-dir TEXT` | *(auto)* | Override directory containing `.toml` configuration presets |

```bash
# Verify all presets in the repository
bforge check

# Verify a single preset during iteration
bforge check half_inch_glass
```

---

### `bforge new-config`

Create a new documented `.toml` configuration preset pre-populated with a `#:schema` header, model docstring, parameter descriptions, `[unit]` annotations, numeric bounds, and default values.

```text
bforge new-config [OPTIONS] NAME
```

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `NAME` *(required)* | — | Preset name (writes `<configs-dir>/<NAME>.toml`, appending `.toml` if omitted) |
| `-m, --model TEXT` | *(auto)* | Target `ModelPrototype` class name (required when multiple models exist and `NAME` does not match one) |
| `-o, --out PATH` | *(auto)* | Explicit destination `.toml` file path |
| `-f, --force` | `False` | Overwrite destination file if it already exists |
| `-c, --configs-dir TEXT` | `configs` | Directory where the new preset is created |
| `--schema-url TEXT` | `../dist/schemas/<Model>.schema.json` | Custom URL or relative path written to `#:schema` |

```bash
# Scaffold configs/quarter_inch_glass.toml
bforge new-config quarter_inch_glass

# Scaffold for a specific model class in a multi-model project
bforge new-config metric_m4 --model SensorBracket
```

---

### `bforge init-agents`

Scaffold a canonical [`AGENTS.md`](src/build123d_forge/templates/AGENTS.template.md) directive in your consumer project root so AI coding agents focus solely on editing `models/*.py` and `configs/*.toml` while delegating builds, B-Rep gates, and preview generation to `bforge`.

```text
bforge init-agents [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `-o, --out PATH` | `AGENTS.md` | Destination file path (or directory, writing `<out>/AGENTS.md`) |
| `-f, --force` | `False` | Overwrite destination file if it already exists |
| `--stdout` | `False` | Print the `AGENTS.md` directive directly to `stdout` instead of disk |

```bash
# Write AGENTS.md at the consumer repository root
bforge init-agents

# Overwrite an existing AGENTS.md with the latest build123d-forge template
bforge init-agents --force

# Print the directive to stdout (or write to GEMINI.md / CLAUDE.md via -o)
bforge init-agents -o GEMINI.md
```

---

### `bforge schema`

Export Draft 2020-12 JSON Schema files (`dist/schemas/<Model>.schema.json`) for editor validation and autocomplete.

```text
bforge schema [OPTIONS] [MODEL]
```

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `[MODEL]` | *(all models)* | Optional `ModelPrototype` class name to export |
| `-o, --out PATH` | `dist/schemas` | Destination directory or `.json` file path |
| `--stdout` | `False` | Print formatted JSON Schema directly to `stdout` instead of disk |
| `--indent INTEGER` | `2` | JSON indentation spaces |
| `--directives / --no-directives` | `--directives` | Include `extends` and `model` loader directives in schema `properties` |

```bash
# Export schemas for all models to dist/schemas/*.schema.json
bforge schema

# Inspect a single model's JSON Schema on stdout
bforge schema AlpstugaShowerMount --stdout
```

---

### `bforge list`

List all discovered `ModelPrototype` classes, resolved configuration variants, source `.toml` files, and live cache validity (`UP-TO-DATE`, `STALE`, or `UNBUILT`).

```text
bforge list [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `-d, --dist PATH` | `dist` | Dist directory inspected for `manifest.json` cache state |
| `--models-dir TEXT` | *(auto)* | Override directory containing model sources |
| `--configs-dir TEXT` | *(auto)* | Override directory containing `.toml` configuration presets |

```bash
bforge list
```

Example output:

```text
======================================================================
build123d-forge Project: alpstuga-shower-mount
======================================================================

Discovered Models:
  • AlpstugaShowerMount  [models/alpstuga_shower_mount.py]

Configuration Presets & Cache Status:
  VARIANT              MODEL                STATUS          CONFIG FILE
  -------------------------------------------------------------------------------
  default              AlpstugaShowerMount  UP-TO-DATE      configs/default.toml
  half_inch_glass      AlpstugaShowerMount  STALE           configs/half_inch_glass.toml
```

---

### `bforge watch`

Poll `models/`, `parts/`, and `configs/` for added, modified, or deleted `.py` and `.toml` files and incrementally rebuild affected variants on save.

```text
bforge watch [OPTIONS] [TARGETS]...
```

| Argument / Option | Default | Description |
| :--- | :--- | :--- |
| `[TARGETS]...` | *(all variants)* | Optional subset of variants or models to rebuild on change |
| `-i, --interval FLOAT` | `1.0` | File-polling interval in seconds |
| `--once` | `False` | Execute a single initial build cycle and exit immediately |
| `--poll-limit, --max-polls INTEGER` | *(unlimited)* | Exit after a fixed number of polling iterations |
| `-o, --output-dir PATH` | `dist` | Output directory for rebuilt artifacts |

```bash
# Continuously rebuild default and half_inch_glass presets every 0.5 s on save
bforge watch -i 0.5 default half_inch_glass
```

---

### `bforge clean`

Delete the `dist/` artifact directory, exported JSON Schemas, and `manifest.json` cache files.

```text
bforge clean [OPTIONS]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `-d, --dist PATH` | `dist/` | Output directory to remove |

```bash
bforge clean
```

---

### `bforge release-package`

Assemble built `dist/<variant>/` directories into a flat GitHub Release upload bundle (`release_assets/`) containing:
- **`<variant>.zip`** — Complete per-variant archive (`*.step`, `*.stl`, `*_print.stl`, `*.png`, `manifest.json`).
- **`<variant>__<asset>`** — Flattened individual CAD and preview files (`.stl`, `.step`, `.stp`, `.png`, `.svg`, `.3mf`) prefixed with `<variant>__` so multiple variants coexist cleanly in GitHub Releases.
- **`RELEASE_NOTES.md`** — Auto-generated Markdown release body featuring a Quick Downloads table, embedded preview grids, B-Rep geometry verification metrics, and resolved parameter tables.
- **`SHA256SUMS.txt`** — Standard SHA-256 checksum file (`sha256sum -c SHA256SUMS.txt`) covering all archives and flat assets.

```text
bforge release-package [OPTIONS] --tag TAG
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `-t, --tag TEXT` *(required)* | — | Release tag version (e.g., `v1.0.0`) |
| `-o, --out PATH` | `release_assets` | Output directory for packaged release bundles |
| `-d, --dist PATH` | `dist` | Built `dist` directory to package from |
| `-r, --repo TEXT` | `[project].name` | Project/repository display name in `RELEASE_NOTES.md` |

```bash
bforge build
bforge release-package --tag v1.0.0
sha256sum -c release_assets/SHA256SUMS.txt
```

---

## Automated GitHub Releases & Embedding Release Previews

### 1. Add the Reusable Release Workflow

Consumer repositories do not need to maintain custom OpenGL, VTK, or release-packaging scripts. Commit a `uv.lock` file (`uv lock`) alongside `.github/workflows/release.yml` calling the reusable workflow from `build123d-forge/build123d-forge`:

```yaml
# .github/workflows/release.yml
name: Build & Release Models

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
  workflow_dispatch:

jobs:
  models:
    name: Build, Validate & Release CAD Models
    uses: build123d-forge/build123d-forge/.github/workflows/build-and-release.yml@main
    permissions:
      contents: write
    with:
      python-version: "3.12"
      models-dir: "models"
      configs-dir: "configs"
      dist-dir: "dist"
      output-assets-dir: "release_assets"
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

#### What Happens Automatically in CI

- **On every Pull Request and `main` push**:
  1. Installs headless OSMesa / OpenGL (`VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow`) and project Python dependencies (`uv sync --frozen` when `uv.lock` is present, or `uv sync`).
  2. Sets `SOURCE_DATE_EPOCH` to the exact Git commit timestamp (`git log -1 --format=%ct`) for bit-for-bit deterministic `.step` headers.
  3. Runs `bforge check` and `bforge build`, then uploads `dist/` as a `model-artifacts` workflow artifact so reviewers can inspect rendered PNGs and CAD solids before merging.
- **When you push a `v*` tag (`git tag v1.0.0 && git push origin v1.0.0`)**:
  1. Runs `bforge release-package --tag "${GITHUB_REF_NAME}"`.
  2. Creates a GitHub Release populated with `RELEASE_NOTES.md`, `<variant>.zip`, flattened `<variant>__*.stl`, `<variant>__*.step`, `<variant>__*.png`, and `SHA256SUMS.txt`.

#### Reusable Workflow Inputs & Secrets

| Input / Secret | Default | Description |
| :--- | :--- | :--- |
| `python-version` | `"3.12"` | Python version installed by `uv` |
| `models-dir` | `""` *(auto)* | Directory containing `ModelPrototype` sources (`models/`, `parts/`, or `[tool.build123d_forge]`) |
| `configs-dir` | `""` *(auto)* | Directory containing `.toml` presets (`configs/` or `[tool.build123d_forge]`) |
| `dist-dir` | `"dist"` | Directory for built CAD artifacts and `manifest.json` |
| `output-assets-dir` | `"release_assets"` | Directory for packaged release assets and `SHA256SUMS.txt` |
| `secrets.token` | `github.token` | GitHub token with `contents: write` permission to publish releases |

---

### 2. Embed Always-Fresh Previews & Downloads in `README.md`

Because `bforge release-package` flattens per-variant assets using the deterministic `<variant>__<filename>` convention, GitHub's permanent `/releases/latest/download/` redirect lets your consumer `README.md` display crisp, shaded renders and direct slicer download links—with **zero binary files committed to Git**:

```text
https://github.com/<org>/<repo>/releases/latest/download/<variant>__<asset>
```

For example, [`build123d-forge/alpstuga-shower-mount`](https://github.com/build123d-forge/alpstuga-shower-mount) embeds a live 4-view gallery and one-click STL/STEP downloads directly from its latest tagged release:

```markdown
| Shower-Side Display & Visor | Diamond Rear Vent & USB-C Cutout |
| :---: | :---: |
| ![Shower-side isometric view](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__iso_shower.png) | ![Rear vent and clip view](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__iso_rear_clip.png) |
| **Inverted Zero-Support Print Orientation** | **Open Bottom & 45° Rest Lips** |
| ![Inverted print-bed orientation](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__print_bed.png) | ![Open bottom airflow view](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__bottom_airflow.png) |

### Quick Downloads (Latest Release)

- **3/8" Glass (`default`):**
  [Ready-to-Print STL](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__alpstuga_shower_mount_print.stl) ·
  [STEP CAD](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default__alpstuga_shower_mount.step) ·
  [Complete `.zip` Bundle](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/default.zip)
- **1/2" Glass (`half_inch_glass`):**
  [Ready-to-Print STL](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/half_inch_glass__alpstuga_shower_mount_print.stl) ·
  [STEP CAD](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/half_inch_glass__alpstuga_shower_mount.step) ·
  [Complete `.zip` Bundle](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest/download/half_inch_glass.zip)
- **All Variants & Checksums:**
  [Latest GitHub Release](https://github.com/build123d-forge/alpstuga-shower-mount/releases/latest)
```

Every time you tag a new release (`v0.3.0`, `v1.0.0`, ...), GitHub automatically redirects `/releases/latest/download/...` to the newly verified renders and CAD exports—keeping your documentation, previews, and downloadable STLs permanently synchronized with your code.

---

## Architecture Overview

`build123d-forge` is structured as a layered, strictly typed pipeline that separates fast, pure-Python static introspection (TOML resolution, Pydantic validation, JSON Schema generation, and AST closure caching) from heavyweight C++ OpenCASCADE B-Rep kernel evaluation and offscreen VTK rendering.

### Layered Subsystem Dependency Graph

```mermaid
flowchart TD
    subgraph Entrypoints["Entrypoints Layer"]
        CLI["cli.py (bforge / build123d-forge Click CLI)"]
        WATCH["watcher.py (Stdlib Polling File Watcher)"]
    end

    subgraph Orchestration["Orchestration Layer"]
        BUILDER["builder.py (Discovery, ThreadPool & B-Rep Gates)"]
        RELEASE["release.py (Zip Archives, SHA256SUMS & Release Notes)"]
    end

    subgraph Subsystems["Core Subsystems Layer"]
        AGENTS["agents.py (Consumer AGENTS.md Template & Scaffolding)"]
        CONFIG["config.py (TOML Loader, extends & Typo Diagnostics)"]
        CACHE["cache.py (AST Closure Hash & BuildManifest Cache)"]
        EXPORTERS["exporters.py (Deterministic STEP & Canonical STL)"]
        RENDER["render.py (Headless VTK, PNG Stripper & 2x2 Grid)"]
        SCHEMA["schema.py (JSON Schema Draft 2020-12 Generator)"]
        SHIM["mcp_shim.py (Static PART_INFO & build123d-mcp Bridge)"]
    end

    subgraph DataModel["Data Model Layer"]
        PROTO["prototype.py (ModelPrototype, Param, ViewSpec, ValidationReport)"]
    end

    CLI --> AGENTS
    CLI --> BUILDER
    CLI --> RELEASE
    CLI --> WATCH
    CLI --> CONFIG
    CLI --> SCHEMA
    CLI --> CACHE
    WATCH --> BUILDER

    BUILDER --> CONFIG
    BUILDER --> CACHE
    BUILDER --> EXPORTERS
    BUILDER --> RENDER
    BUILDER --> SCHEMA
    BUILDER --> PROTO
    RELEASE -.->|"reads manifest.json & dist artifacts"| CACHE

    CONFIG -.->|"instantiates & validates"| PROTO
    EXPORTERS --> PROTO
    RENDER --> PROTO
    SCHEMA --> PROTO
    SHIM --> PROTO
```

### Module Responsibilities

All library source lives under `src/build123d_forge/`:

| Module | Key Public Symbols | Primary Responsibility |
| :--- | :--- | :--- |
| `__init__.py` | `__version__`, `__all__` (57 exports) | Curates the single canonical public API surface with zero duplicate legacy aliases. |
| `agents.py` | `get_agents_directive`, `scaffold_agents` | Loads and scaffolds the canonical consumer `AGENTS.md` directive (`templates/AGENTS.template.md`). |
| `prototype.py` | `ModelPrototype`, `Param`, `ViewSpec`, `ValidationReport` | Immutable Pydantic v2 base prototype, physical unit metadata (`unit`), B-Rep `verify()`, camera `views()`, `orient_for_print()` hook, and `.toml` template rendering. |
| `schema.py` | `generate_json_schema`, `serialize_json_schema`, `export_json_schema` | Emits JSON Schema Draft 2020-12 definitions enforcing `additionalProperties: false` and optional `extends` / `model` IDE directives. |
| `config.py` | `load_config_file`, `read_config_file`, `scaffold_config`, `ConfigError` | Recursive `tomllib` configuration loading, cycle-checked `extends` deep-merging, directive stripping, and `difflib` + synonym fuzzy typo diagnostics. |
| `cache.py` | `BuildManifest`, `compute_ast_closure_hash`, `compute_param_hash`, `compute_manifest`, `is_cache_valid`, `clear_cache` | Transitive local Python AST import closure hashing, canonical float/JSON parameter hashing, memoized artifact SHA-256 verification, and sub-5 ms warm cache checks. |
| `exporters.py` | `export_deterministic_step`, `export_canonical_stl`, `export_print_stl`, `normalize_step_file`, `make_canonical_stl_header` | `SOURCE_DATE_EPOCH` STEP header & assembly occurrence normalization, canonical 80-byte binary STL headers with locked mesh tolerances, and print-bed STL dispatch. |
| `render.py` | `render_view`, `render_views`, `render_preview_grid`, `strip_png_chunks`, `strip_png_metadata` | Headless offscreen VTK Phong rendering, ancillary PNG chunk stripping, solid-background fallback, and `2x2` `preview_grid.png` composition. |
| `mcp_shim.py` | `mcp_shim`, `generate_part_info`, `generate_part_info_code`, `generate_part_info_ast` | Bridges declarative `ModelPrototype` classes into `build123d-mcp` and procedural scripts (`PART_INFO`, `make()`, `make_print_oriented()`, ForwardRef `model_rebuild`). |
| `builder.py` | `build_project`, `build_variant`, `check_project`, `list_project`, `clean_dist`, `discover_models`, `discover_variants` | Project/model/config auto-discovery, `ThreadPoolExecutor` parallel variant builds, incremental cache short-circuiting, and `bforge check` validation gates. |
| `release.py` | `package_release`, `generate_release_notes` | Packages `dist/<variant>/` into per-variant `.zip` bundles, flattened `<variant>__<asset>` files, `RELEASE_NOTES.md`, and `SHA256SUMS.txt`. |
| `watcher.py` | `watch_and_rebuild` | Zero-dependency stdlib `(st_mtime, st_size)` file poller over `.py` and `.toml` sources for live incremental rebuilds. |
| `cli.py` | `cli`, `main` | `Click` command group powering `bforge` and `build123d-forge` (`build`, `check`, `new-config`, `init-agents`, `schema`, `list`, `clean`, `watch`, `release-package`). |

### Core Architectural Principles

1. **Strict Immutability & Schema Hygiene (`frozen=True`, `extra="forbid"`)**:
   `ModelPrototype` configures Pydantic v2 with `ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=False, validate_default=True)` (and `ViewSpec` likewise enforces `frozen=True, extra="forbid"`). Freezing instances guarantees thread safety across `ThreadPoolExecutor` worker pools in `build_project()` and prevents accidental parameter mutation inside `build()` or `orient_for_print()`. Forbidding extra inputs ensures misspelled `.toml` keys fail fast with actionable fuzzy suggestions rather than silently falling back to defaults.
2. **Canonical Public Exports (`__init__.py`)**:
   Every supported class, function, and exception is explicitly re-exported in `build123d_forge.__all__` without redundant aliases. Contract tests in `tests/test_imports.py` enforce symbol presence, Semantic Versioning, and editable `src/` path resolution.
3. **Decoupling Fast-Path Introspection from OpenCASCADE & VTK**:
   `cache.py`, `config.py`, `schema.py`, `agents.py`, and `release.py` avoid top-level `build123d` geometry evaluation and `vtk` render window initialization (`vtk` is imported lazily inside `render_view()`, and `OCP` version lookup in `get_dependency_versions()` is guarded behind `@functools.lru_cache(maxsize=1)`). Commands like `bforge list`, `bforge schema`, `bforge new-config`, `bforge init-agents`, and warm incremental `bforge build` hits execute purely in Python metadata space without spinning up OpenCASCADE B-Rep kernels or OpenGL contexts.

---

## Internals Deep Dive

### Content-Addressed Caching & Incremental Pipeline

During `bforge build`, each discovered `VariantSpec` is evaluated against its on-disk `dist/<variant>/manifest.json` before any CAD solid construction or tessellation occurs:

```mermaid
sequenceDiagram
    autonumber
    participant CLI as cli.py
    participant Builder as builder.py
    participant Config as config.py
    participant Cache as cache.py
    participant CAD as OpenCASCADE and VTK

    CLI->>Builder: Dispatch VariantSpec via build_project()
    Builder->>Config: load_config_file() resolves extends & validates ModelPrototype
    Config-->>Builder: Frozen ModelPrototype instance

    alt manifest.json exists and not --force
        Builder->>Cache: compute_manifest(model_instance, source_file)
        Note over Cache: BFS AST import closure + canonical param_hash + dependency versions
        Cache-->>Builder: Tentative BuildManifest (input_sha256)
        Builder->>Cache: is_cache_valid(manifest_path, tentative)
        Note over Cache: Match input_sha256 & verify artifact SHA-256s via _ARTIFACT_HASH_CACHE
        alt Cache Valid (~1.5 ms warm hit)
            Cache-->>Builder: True (short-circuit OpenCASCADE & VTK)
            Builder-->>CLI: BuildResult(cached=True)
        else Cache Stale or Artifact Tampered
            Cache-->>Builder: False
            Builder->>CAD: build() -> verify() -> STEP & STL -> VTK 2x2 grid -> JSON Schema
            CAD-->>Builder: Deterministic CAD, PNG & Schema files
            Builder->>Cache: compute_manifest(..., artifacts, metrics) & save()
            Builder-->>CLI: BuildResult(cached=False)
        end
    else Unbuilt Variant or --force
        Builder->>CAD: build() -> verify() -> STEP & STL -> VTK 2x2 grid -> JSON Schema
        CAD-->>Builder: Deterministic CAD, PNG & Schema files
        Builder->>Cache: compute_manifest(..., artifacts, metrics) & save()
        Builder-->>CLI: BuildResult(cached=False)
    end
```

Key mechanisms inside `src/build123d_forge/cache.py`:

- **Transitive AST Closure & Formatting Invariance (`compute_ast_closure_hash`)**: `_ImportExtractor(ast.NodeVisitor)` performs a cycle-safe BFS across local workspace modules (`current_dir`, `project_root`, `project_root / "src"`, and relative imports), excluding stdlib and `.venv`/`site-packages`/`.git`/`dist`/`__pycache__` via `_is_local_file()`. Visited files are sorted by relative path and hashed as `FILE:<rel_path>\nAST:<ast_dump>\n` using `ast.dump(tree, include_attributes=False)`—ignoring `#` comments, blank lines, and `CRLF`/`LF` line endings while catching any semantic change across transitively imported helpers. *(Non-Python binary inputs in `resources/` are outside the AST closure; rebuild with `bforge build --force` when replaced.)*
- **Sideband Missing-Candidate Tracking (`_IMPORT_PRIOR_CANDIDATES` & `_AST_MISSING_CANDIDATES`)**: Thread-safe caches (`_AST_CLOSURE_CACHE`, `_IMPORT_RESOLVE_CACHE` under `_CACHE_LOCK`) record every higher-priority path probed *before* a resolved module (e.g., `from pkg import submod` falling back to `pkg/__init__.py` before `pkg/submod.py` exists). Warm lookups check `any(mc.is_file() for mc in missing_cands)` alongside `(st_mtime_ns, st_size, sha256)` to detect newly created, deleted, or shadowed modules immediately.
- **Canonical Parameter Hashing & ~1.5 ms Warm Hits (`compute_param_hash`, `BuildManifest`)**: `canonicalize_params()` sorts dict keys, normalizes `-0.0` to `0.0`, handles `NaN`/`Inf`, and rounds floats via `round(obj, 12)` before compact `json.dumps` + SHA-256 hashing. `compute_manifest()` combines `ast_hash`, `param_hash`, and core package versions (`python`, `build123d`, `pydantic`, `vtk`, `cadquery_ocp`) into `input_sha256`; `is_cache_valid()` verifies manifest digests and memoized artifact SHA-256s (`_ARTIFACT_HASH_CACHE`) in ~1.5 ms.

### Bit-for-Bit Deterministic CAD & Rendering

- **`ISO-10303-21` STEP Normalization (`exporters.py`)**: `export_deterministic_step()` resolves the timestamp from `epoch`, `SOURCE_DATE_EPOCH`, or `0` (`format_step_timestamp()`), then `normalize_step_file()` normalizes multi-line/escaped `FILE_NAME` headers (`_STEP_FILE_NAME_PATTERN`) and renumbers `NEXT_ASSEMBLY_USAGE_OCCURRENCE` IDs `'1'..'N'` (`_STEP_ASSEMBLY_USAGE_PATTERN`) to neutralize OpenCASCADE's static assembly counter.
- **Canonical Binary STL & Print Dispatch (`exporters.py`)**: `export_canonical_stl()` meshes with locked tolerances (`linear_deflection=0.01` mm, `angular_deflection=0.05` rad) and writes an 80-byte null-padded UTF-8 header (`build123d-forge:<model>:<config>` via `make_canonical_stl_header()`). `export_print_stl()` emits `<slug>_print.stl` (or `<slug>_<part>_print.stl`) whenever `orient_for_print()` returns geometry.
- **Headless Offscreen VTK, Chunk Stripping & `2x2` Grids (`render.py`)**: `render_view()` tessellates solids (`_tessellate_to_mesh()`), computes `FeatureAngle=60.0` Phong normals (`#ADD8E6` mesh on `#FAFAFA` background), and renders offscreen with `SetMultiSamples(0)` (using `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow` for software OSMesa/EGL, or falling back to a deterministic Pillow PNG; `[render]` adds `resvg-py>=0.5.0` for SVG workflows). `strip_png_chunks()` strips ancillary chunks (`tIME`, `tEXt`, `zTXt`, `iTXt`, `eXIf` in `DEFAULT_STRIP_CHUNKS`) with header resynchronization and `IEND` checks, and `render_preview_grid()` composites the first 4 `1000x750` views into a `2000x1500` `2x2` grid with 2 px `#DCDCDC` dividers.

### B-Rep Verification Gates & `build123d-mcp` Shim

- **B-Rep Gates (`ModelPrototype.verify` & `check_project`)**: `verify()` checks single solids or `dict[str, Part]` assemblies via OpenCASCADE `BRepCheck_Analyzer` (`is_valid`), closed-shell manifoldness (`is_manifold`, `watertight`, `len(solids) > 0`), and strictly positive solid volume (`volume_mm3 > 0.0`), returning an immutable `ValidationReport` with `bounding_box_mm`. `bforge check` enforces both config validation (`config_valid`) and B-Rep verification (`geometry_valid`), exiting `1` on any failure.
- **`build123d-mcp` Shim (`mcp_shim.py`)**: `generate_part_info()`, `generate_part_info_code()`, and `generate_part_info_ast()` serialize `model_fields` via `_to_safe_literal()` into an `ast.literal_eval()`-safe `PART_INFO` dict. `mcp_shim()` runs `cls.model_rebuild(_types_namespace=module_globals)` for `from __future__ import annotations` compatibility, injects `PART_INFO`, `make(**kwargs)`, and `make_print_oriented(**kwargs)`, and binds `result = make()` inside `build123d-mcp` sessions.

---

## Development Setup

### System Prerequisites

- **Python**: `>= 3.11` (verified in CI against Python `3.11` and `3.12`).
- **Headless OpenGL / OSMesa Libraries (Ubuntu / Debian)**: Required by OpenCASCADE (`cadquery-ocp`) and offscreen software VTK rendering:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgl1 libegl1 libosmesa6 libgomp1
```

### Cloning & Editable Installation

`build123d-forge` uses a standards-based PEP 517 / PEP 660 `src/` layout built with `hatchling`. Use [`uv`](https://docs.astral.sh/uv/) to create an isolated virtual environment and install the package in editable mode with `[render,dev]` (and optional `[mcp]`) extras:

```bash
git clone https://github.com/build123d-forge/build123d-forge.git
cd build123d-forge

# Create virtual environment and install editable package + render/dev extras
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[render,dev]"

# Verify CLI entrypoints
bforge --version
build123d-forge --help
```

---

## Testing Architecture & Writing Future Tests

### Test Suite Organization

All tests reside under `tests/` (configured via `testpaths = ["tests"]` in `pyproject.toml`) and are split into two complementary layers:

1. **Subsystem & Contract Unit Suites (`tests/test_<module>.py`)**:
   - `tests/test_imports.py`: Verifies `__version__` SemVer compliance, canonical `__all__` exports, removal of deprecated aliases, and editable `src/build123d_forge` resolution.
   - `tests/test_prototype.py`: Tests `ModelPrototype` immutability, `Param` bounds/units, `@model_validator` cross-field invariants, `verify()` B-Rep diagnostics, and `to_toml_template()`.
   - `tests/test_schema.py`: Validates Draft 2020-12 meta-schema compliance (`jsonschema.Draft202012Validator`), numeric bounds mapping, `additionalProperties: false`, and IDE directives.
   - `tests/test_config.py`: Covers `.toml` loading, recursive `extends` deep-merging, circular inheritance detection, `difflib` + `DEFAULT_SYNONYMS` / `__synonyms__` typo suggestions (including nested union submodels), and config scaffolding.
   - `tests/test_cache.py`: Tests transitive AST closure walking, comment/CRLF invariance, `_IMPORT_PRIOR_CANDIDATES` / `_AST_MISSING_CANDIDATES` dynamic invalidation, `< 50 ms` warm cache performance, and `ThreadPoolExecutor` race freedom.
   - `tests/test_exporters.py`: Verifies `SOURCE_DATE_EPOCH` STEP timestamp & assembly occurrence normalization, 80-byte canonical binary STL headers, and `export_print_stl()` single/multi-part dispatch.
   - `tests/test_render.py`: Tests headless VTK Phong rendering, `strip_png_chunks()` metadata removal and corrupted-header resynchronization, and `2x2` `preview_grid.png` compositing.
   - `tests/test_mcp_shim.py`: Verifies `ast.literal_eval()` safety of `generate_part_info_ast()`, `model_rebuild()` ForwardRef resolution, `make()` / `make_print_oriented()`, and MCP `result` hooks.
   - `tests/test_agents.py`: Validates consumer `AGENTS.md` template integrity, rule budget size limits, live Click CLI command/flag conformance across embedded examples, and `scaffold_agents()` / `bforge init-agents` behavior.
   - `tests/test_builder.py`, `tests/test_release.py`, `tests/test_cli.py`: Exercise model/variant discovery, incremental and parallel `workers=2` builds, release archive & `SHA256SUMS.txt` packaging, and all 9 `bforge` Click subcommands via `CliRunner`.
   - `tests/test_workflows.py`: Parses `.github/workflows/ci.yml` and `.github/workflows/build-and-release.yml` offline (zero external YAML dependencies) and validates every `bforge` invocation and flag against the live Click command specification.

2. **Four-Tier End-to-End Verification Matrix (`tests/test_e2e_tier*.py`)**:
   - `tests/test_e2e_tier1_features.py` (**Tier 1 — Feature Isolation**): Validates each core feature in isolation (`>= 5` cases per subsystem).
   - `tests/test_e2e_tier2_boundaries.py` (**Tier 2 — Boundaries & Invalid Inputs**): Exercises numeric boundary limits, circular `extends` chains, non-manifold/open-shell B-Rep failures, malformed binary streams, and fuzzy-matching length guards.
   - `tests/test_e2e_tier3_combinations.py` (**Tier 3 — Pairwise Combinations**): Tests cross-module interactions (`extends` inheritance + `@model_validator` invariants + `bforge check` + AST/parameter cache invalidation).
   - `tests/test_e2e_tier4_scenarios.py` (**Tier 4 — Production Lifecycles**): Executes complete multi-step release engineering scenarios (including the `AlpstugaShowerMount` reference workflow from prototype authoring through `bforge release-package` archive and checksum verification).

### Running the Test Suite

```bash
# Run the complete unit + 4-tier E2E suite
pytest -v

# Run a targeted subsystem file or filter by keyword expression
pytest tests/test_cache.py -k "late_subpackage or multithreaded" -v

# Run under headless software VTK rendering (matches CI on systems without a display server)
VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow pytest -v

# Verify bit-for-bit STEP, STL, and PNG reproducibility with a pinned epoch
SOURCE_DATE_EPOCH=1700000000 VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow pytest tests/test_exporters.py tests/test_render.py -v
```

### Contributor Checklist for Future Features & Tests

- **Preserve Determinism Invariants**: Never introduce wall-clock timestamps, unsorted set/dict serialization, process-dependent counters, or unstripped PNG metadata into build or release outputs. Always route CAD exports through `export_deterministic_step()` / `export_canonical_stl()` and pass rendered PNG buffers through `strip_png_chunks()`.
- **Verify AST & Cache Invalidation Edge Cases**: Call `clear_cache()` in test `setUp()` methods when benchmarking or testing cache state transitions. When modifying import resolution or manifest schemas in `cache.py`, test comment/CRLF invariance alongside dynamic file creation, deletion, and `__init__.py` fallback overrides (`_IMPORT_PRIOR_CANDIDATES` / `_AST_MISSING_CANDIDATES`), and keep warm `is_cache_valid()` execution well under the 50 ms budget without importing OpenCASCADE or VTK on the fast path.
- **Keep Workflow & Public Export Conformance Green**: `tests/test_workflows.py` (`TestWorkflowCLICommandConformance`) introspects `build123d_forge.cli.cli` directly—any new or renamed Click option used in `.github/workflows/ci.yml` or `.github/workflows/build-and-release.yml` must match `cli.py` exactly. Likewise, update `build123d_forge.__all__` and `tests/test_imports.py` together when adding public API symbols.
- **Maintain Python 3.11 & 3.12 Matrix Compatibility**: Use stdlib `tomllib` for TOML parsing, ensure type introspection (`_resolve_type_name()` and `mcp_shim` `model_rebuild()`) supports both `typing.Union` and PEP 604 `X | Y` unions under `from __future__ import annotations`, isolate filesystem tests with `tmp_path` / `tempfile.TemporaryDirectory` / `CliRunner.isolated_filesystem()`, and verify headless compatibility with `VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow`.

---

## License

Released under the **MIT License**. Part of the [`build123d-forge`](https://github.com/build123d-forge) organization.
