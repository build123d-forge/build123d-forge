# AGENTS.md — Parametric CAD Workflow with `build123d-forge`

This repository manages parametric [`build123d`](https://github.com/gumyr/build123d) hardware with **[`build123d-forge`](https://github.com/build123d-forge/build123d-forge)** (`bforge`).

## Role Split & Guardrails

**You (the Agent) edit only declarative Python geometry (`models/*.py`) and sparse preset configs (`configs/*.toml`)**, delegating schemas, validation, STEP/STL exports, and shaded VTK PNG previews to `bforge`. Because all build and camera logic lives in `ModelPrototype` + `configs/*.toml`, humans tweaking `.py` or `.toml` values manually later get the exact same automated builds, print-oriented STLs, and previews without an agent in the loop.

- **No custom build, export, or render scripts**: Never create `generate_*.py` scripts, `export_step()`/`export_stl()` wrappers, or ad-hoc VTK/Matplotlib/Pillow/SVG rendering code. Always delegate to `bforge build` (or `bforge watch`).
- **No one-off throwaway preview renders**: If a feature or print orientation is unclear in `dist/<variant>/preview_grid.png` or `<stem>.png`, add or adjust a `ViewSpec` in `ModelPrototype.views()` and re-run `bforge build` so that camera view stays permanently automated.
- **Keep Git 100% text-only for generated outputs**: Keep `dist/` and `release_assets/` in `.gitignore` and never commit generated `.step`, `.stl`, or `.png` files (only external inputs like reference scans or COTS mating STEP models in `resources/` belong in Git). For consumer `README.md` embeds/downloads, link `https://github.com/<org>/<repo>/releases/latest/download/<variant>__<asset>` powered by `build123d-forge`'s reusable `build-and-release.yml` workflow.
- **No magic variant constants or model I/O**: Declare tunable dimensions via `Param(..., unit="mm", description="...")` and sparse `configs/<variant>.toml` presets. Keep `models/*.py` pure and immutable (`frozen=True`, `extra="forbid"`—never mutate `self` or read `.toml` files, env vars, or output paths).
- **Helper modules vs. `ModelPrototype` discovery**: Every concrete `ModelPrototype` subclass in `models/` (or `parts/`) is auto-discovered as a standalone build target. Implement reference/mating COTS geometry that should not build as its own variant as plain Python helper functions/classes (still tracked by the AST import cache).

---

## Project Layout & Authoring Patterns

```text
<project-root>/
├── AGENTS.md                       # Agent workflow directive
├── pyproject.toml                  # Dependencies (build123d, build123d-forge[render]) & [tool.build123d_forge]
├── models/<part_slug>.py           # Committed ModelPrototype subclass(es) + optional helper modules
├── configs/<variant>.toml          # Committed sparse parameter presets (e.g. default.toml)
├── resources/                      # Optional committed reference inputs (scans, COTS mating STEP files)
└── dist/                           # Git-ignored outputs generated exclusively by `bforge`
    ├── schemas/<Model>.schema.json
    └── <variant>/                  # <slug>.step, <slug>.stl, <slug>_print.stl, <stem>.png, preview_grid.png, manifest.json
```

*(When bootstrapping a new repository, add `dependencies = ["build123d>=0.13.0", "build123d-forge[render]>=0.1.0"]` and `[tool.build123d_forge]` with `models = ["models"]` and `configs = ["configs"]` in `pyproject.toml`, and ignore `dist/`, `release_assets/`, and `.venv/` in `.gitignore`.)*

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
        """Place the support-free print face coplanar on Z = 0 (emits *_print.stl)."""
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

- **Assemblies**: Returning `dict[str, Part]` from `build()` (and `orient_for_print()`) exports a combined `<slug>.step` `Compound` plus per-part `<slug>_<key>.stl` meshes.
- **`ViewSpec` camera angles**: At `(azimuth=0, elevation=0)`, the camera sits on `-Y` looking toward `+Y` with `+Z` up (`+X` right). `direction` (`"front"`, `"iso"`, `"top"`, `"bottom"`, `"side"`/`"right"`, `"left"`, `"back"`/`"rear"`) sets a preset angle **only** when `azimuth == 0` or `elevation == 0`; non-zero `azimuth`/`elevation` are **absolute** spherical angles in degrees (e.g., use `azimuth=145, elevation=25` for rear-iso, and always supply explicit angles with `"iso"`).

### 2. Sparse Configuration Presets (`configs/*.toml`)

```toml
# configs/wide_right.toml
#:schema ../dist/schemas/MountingBracket.schema.json

extends = "default.toml"
width = 65.0
handedness = "right"
```

- Omit unchanged fields to inherit from `extends` or prototype defaults; in multi-model repos, disambiguate with `model = "MountingBracket"` (or `configs/<model_slug>/` subdirectories).

---

## Standard Iteration Loop

*(Run `bforge` directly, or via `uv run bforge` / `.venv/bin/bforge` inside a project virtualenv.)*

```bash
bforge list                         # 1. Inspect discovered models, variants & cache status
bforge new-config <variant_name>    # 2. Optional: scaffold a documented preset in configs/
bforge schema                       # 3. Optional: refresh dist/schemas/*.schema.json after Param edits
bforge check                        # 4. Enforce Pydantic + B-Rep manifold/watertightness gates
bforge build                        # 5. Build STEP/STL/*_print.stl + PNG views & preview_grid.png (add -j 4 or <variant>)
VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow bforge build  # Headless Linux / CI software VTK
bforge build --force                # Force rebuild when external non-Python resources/*.step files change
```

- **Always run `bforge check` alongside `bforge build`**: `bforge build` still exports `.step`/`.stl` on broken geometry for debugging and suppresses preview-render exceptions, whereas `bforge check` is the strict non-zero-exit gate (`is_valid`, `is_manifold`, `watertight`, `volume_mm3 > 0`).
- **Verify previews & metrics**: View `dist/<variant>/preview_grid.png` and `<view_stem>.png` with your image viewer tool (confirming PNGs were written) and inspect `dist/<variant>/manifest.json` (`resolved_params` and `metrics`: `bounding_box_mm`, `volume_mm3`, `solids`, `faces`, `watertight`).
- **Live watcher compatibility**: If `bforge watch` is running in the background, saving `.py`/`.toml` files rebuilds automatically; still run `bforge check` and `bforge build` (`< 5 ms` warm cache hit) to confirm clean status before finishing.

---

## Optional Interactive Diagnostics (`build123d-mcp`)

Keep `MyModel.model_rebuild()` and `mcp_shim(MyModel, globals())` at the bottom of `models/*.py` (exposing `PART_INFO`, `make(**kwargs)`, `make_print_oriented(**kwargs)`, and `result`). When `build123d-mcp` is connected, use it **only** for interactive geometric queries (`execute_file`, `measure`, `cross_sections`, `compare`, `find_holes`, `design_audit`, `analyze_printability`)—never treat MCP `export()` or `render_view()` as project deliverables, and always finish with `bforge check` + `bforge build`.
