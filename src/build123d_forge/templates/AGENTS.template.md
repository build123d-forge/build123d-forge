# AGENTS.md — Parametric CAD Workflow with `build123d-forge`

This repository manages parametric [`build123d`](https://github.com/gumyr/build123d) hardware using **[`build123d-forge`](https://github.com/build123d-forge/build123d-forge)** (`bforge`).

## Core Division of Responsibilities

- **What You (the Agent) Edit**:
  1. **Declarative Python Geometry (`models/*.py`)**: Subclass `ModelPrototype`, declare typed parameters with `Param(...)`, enforce geometric invariants with `@model_validator`, implement upright CAD in `build()`, define slicer bed orientation in `orient_for_print()`, and declare preview cameras in `views()`.
  2. **Sparse Preset Configurations (`configs/*.toml`)**: Capture size, clearance, or handedness variants as minimal `.toml` overrides (inheriting via `extends` where appropriate).
- **What You Delegate to `build123d-forge` (`bforge`)**:
  - Draft 2020-12 JSON Schema generation (`bforge schema` -> `dist/schemas/<Model>.schema.json`)
  - Parameter bounds, cross-field invariant checks, fuzzy TOML typo diagnostics, and OpenCASCADE B-Rep manifold/watertightness gates (`bforge check`)
  - Incremental AST + parameter SHA-256 cached `.step`, `.stl`, and `*_print.stl` exports (`bforge build` / `bforge watch`)
  - Offscreen Phong-shaded VTK preview PNGs (`dist/<variant>/<stem>.png`) and `2×2` composite grids (`dist/<variant>/preview_grid.png`)
  - Deterministic GitHub Release archives and checksums (`bforge release-package`)

Delegating all building and rendering to `bforge` ensures that whenever a human later tweaks a Python feature or `.toml` parameter manually—without an agent in the loop—they get the exact same automated builds, print-oriented STLs, and multi-angle PNG previews.

---

## Strict Guardrails (Do / Do Not)

1. **DO NOT write custom build, export, or preview scripts**:
   - Never create bespoke `generate_*.py` scripts, custom `export_step()` / `export_stl()` wrappers, or ad-hoc VTK, Matplotlib, Pillow, or SVG rendering scripts.
   - Always run `bforge build` (or rely on `bforge watch`) to produce CAD exports and PNG previews.
2. **DO NOT render one-off throwaway views in place of `ModelPrototype.views()`**:
   - If a feature, cutout, or print orientation is not clearly visible in `dist/<variant>/preview_grid.png` or `dist/<variant>/<stem>.png`, add or adjust a `ViewSpec` inside `ModelPrototype.views()` and re-run `bforge build` so that camera angle remains permanently automated.
3. **DO NOT commit generated build or preview artifacts to Git**:
   - Keep `dist/` and `release_assets/` in `.gitignore`. Never commit generated `.step`, `.stl`, or `.png` renders.
   - Only external input references (such as 3D scans, datasheets, or COTS mating STEP parts in `resources/`) belong in version control.
4. **DO NOT hard-code variant dimensions or duplicate `.py` files per variant**:
   - Expose tunable dimensions on the `ModelPrototype` with `Param(..., unit="mm", description="...")` and define variants as sparse `configs/<variant>.toml` presets.
5. **DO NOT mutate `self` or perform file/env I/O inside `models/*.py`**:
   - `ModelPrototype` enforces `frozen=True` and `extra="forbid"`. Never read `.toml` files, environment variables, or output paths inside model modules; read validated attributes from `self`.

---

## Project Layout & Authoring Patterns

```text
<project-root>/
├── AGENTS.md                       # This directive
├── pyproject.toml                  # Dependencies (build123d, build123d-forge[render]) & [tool.build123d_forge]
├── models/                         # Committed declarative ModelPrototype Python modules
│   └── <part_slug>.py
├── configs/                        # Committed sparse .toml parameter presets
│   ├── default.toml
│   └── <variant>.toml
├── resources/                      # Optional committed reference inputs (scans, COTS mating STEP files)
└── dist/                           # Git-ignored outputs generated exclusively by `bforge`
    ├── schemas/<Model>.schema.json
    └── <variant>/
        ├── <part_slug>.step        # Deterministic upright ISO-10303-21 B-Rep CAD
        ├── <part_slug>.stl         # Canonical binary upright mesh
        ├── <part_slug>_print.stl   # Slicer-ready mesh oriented on Z = 0 (when orient_for_print is defined)
        ├── <view_stem>.png         # Individual 1000x750 shaded VTK camera renders
        ├── preview_grid.png        # 2000x1500 2x2 composite of the first 4 ViewSpec cameras
        └── manifest.json           # Input/artifact SHA-256 cache + B-Rep metrics (volume, bbox, faces)
```

*(If bootstrapping a new repo, configure `pyproject.toml` with `dependencies = ["build123d>=0.13.0", "build123d-forge[render]>=0.1.0"]` and `[tool.build123d_forge]` setting `models = ["models"]` and `configs = ["configs"]`, and ignore `dist/`, `release_assets/`, and `.venv/` in `.gitignore`.)*

### 1. Canonical `ModelPrototype` (`models/<part_slug>.py`)

```python
from typing import Literal, Self

from build123d import Align, Axis, Box, BuildPart, Part
from pydantic import model_validator

from build123d_forge import ModelPrototype, Param, ViewSpec, mcp_shim


class MountingBracket(ModelPrototype):
    """Concise summary of the parametric part and its coordinate conventions."""

    __synonyms__ = {"thickness": "wall_thickness"}

    width: float = Param(40.0, gt=0.0, unit="mm", description="Overall bracket width")
    wall_thickness: float = Param(3.0, ge=1.2, unit="mm", description="Structural wall thickness")
    handedness: Literal["left", "right"] = Param("left", description="Mounting side")

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.width < 3.0 * self.wall_thickness:
            raise ValueError("width must be at least 3x wall_thickness.")
        return self

    def build(self) -> Part:
        with BuildPart() as bp:
            Box(self.width, 25.0, self.wall_thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
        part = bp.part
        part.label = "mounting_bracket"
        return part

    def orient_for_print(self, part: Part | None = None) -> Part:
        """Place the optimal support-free print face coplanar on Z = 0 (emits *_print.stl)."""
        if part is None:
            part = self.build()
        oriented = part.rotate(Axis.X, 0.0)
        oriented.label = "mounting_bracket_print_oriented"
        return oriented

    def views(self) -> list[ViewSpec]:
        """4+ views automatically composite the first 4 into dist/<variant>/preview_grid.png."""
        return [
            ViewSpec(stem="iso_front", description="Front isometric", direction="front", azimuth=35.0, elevation=25.0),
            ViewSpec(stem="iso_rear", description="Rear isometric", direction="front", azimuth=145.0, elevation=25.0),
            ViewSpec(stem="top", description="Top plan view", direction="top", azimuth=0.0, elevation=0.0),
            ViewSpec(
                stem="print_bed",
                description="Print-bed orientation (Z = 0)",
                direction="iso",
                azimuth=35.0,
                elevation=30.0,
                use_print_oriented=True,
            ),
        ]


MountingBracket.model_rebuild()
mcp_shim(MountingBracket, globals())
```

- **Multi-part assemblies**: `build()` (and `orient_for_print()`) may return `dict[str, Part]`, which exports a combined `<slug>.step` `Compound` plus per-part `<slug>_<key>.stl` meshes.
- **`ViewSpec.direction` choices**: `"front"`, `"iso"`, `"top"`, `"bottom"`, `"side"` (or `"right"`), `"left"`, `"back"` (or `"rear"`).

### 2. Sparse Configuration Presets (`configs/*.toml`)

```toml
# configs/wide_right.toml
#:schema ../dist/schemas/MountingBracket.schema.json

extends = "default.toml"
width = 65.0
handedness = "right"
```

- Omit unchanged parameters so presets inherit cleanly from `extends` or `ModelPrototype` defaults.
- In multi-model repositories, bind a preset explicitly with `model = "MountingBracket"` (or match by `configs/<model_slug>/` directory).

---

## Standard Agent Iteration Loop

*(Invoke `bforge` directly if on `PATH`, or via `uv run bforge` / `.venv/bin/bforge` inside a project virtual environment.)*

1. **Inspect Project & Cache State**:
   ```bash
   bforge list
   ```
   Review discovered `ModelPrototype` classes, `configs/*.toml` variants, and cache status (`UP-TO-DATE`, `STALE`, `UNBUILT`).

2. **Edit Python Model & TOML Configs**:
   - Edit geometry, `Param` fields, invariants, print orientation, or `ViewSpec` cameras in `models/*.py`.
   - Edit preset values in `configs/*.toml`, or scaffold a new documented preset from prototype defaults:
     ```bash
     bforge new-config <variant_name>
     ```
   - Export updated Draft 2020-12 JSON Schemas if you changed `Param` definitions (`bforge build` also updates schemas automatically):
     ```bash
     bforge schema
     ```

3. **Delegate Validation, CAD Builds & Preview Rendering to `bforge`**:
   - Run fast parameter + OpenCASCADE B-Rep manifold/watertightness/volume gates:
     ```bash
     bforge check
     ```
   - Incrementally build changed variants and generate shaded PNG previews into `dist/<variant>/`:
     ```bash
     bforge build
     ```
     *(Target a single variant during tight iteration with `bforge check <variant>` and `bforge build <variant>`, or parallelize across variants with `bforge build -j 4`.)*
   - **Headless Linux / Container Environments**: If no X11/Wayland display is attached, run offscreen software VTK rendering with:
     ```bash
     VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow bforge build
     ```
   - **Live Watcher Workflow**: If a background `bforge watch` process is already running, saving `models/*.py` or `configs/*.toml` triggers incremental rebuilds automatically. Run `bforge check` and `bforge build` (a `< 5 ms` cache hit once up-to-date) to confirm zero errors before concluding.

4. **Inspect Generated Previews & Verification Metrics**:
   - View `dist/<variant>/preview_grid.png` and individual `dist/<variant>/<view_stem>.png` files with your image/file reading tool to visually verify silhouette, feature alignment, clearances, and `Z = 0` print-bed seating.
   - Read `dist/<variant>/manifest.json` (`metrics` and `resolved_params`) to verify `volume_mm3`, `bounding_box_mm`, `solids`, `faces`, `is_valid`, `is_manifold`, and `watertight`.

---

## Optional Interactive Diagnostics with `build123d-mcp`

When the `build123d` MCP server is available in your session:

- Always keep `MyModel.model_rebuild()` and `mcp_shim(MyModel, globals())` at the bottom of `models/*.py`. This exposes `PART_INFO`, `make(**kwargs)`, `make_print_oriented(**kwargs)`, and `result` directly from the `ModelPrototype` without duplicating parameter defaults.
- Use `build123d-mcp` tools (`execute_file`, `measure`, `cross_sections`, `compare`, `find_holes`, `design_audit`, `analyze_printability`) **only for interactive geometric inspection, section cuts, mating-part clearance checks, and FDM printability analysis**.
- Never treat MCP `export()` or `render_view()` calls as final deliverables. Persist all geometry and camera changes in `models/*.py` and `configs/*.toml`, and always finish with `bforge check` and `bforge build` so `dist/<variant>/` CAD files and PNG previews are reproducibly built and verified.
