"""Headless VTK offscreen rendering, deterministic PNG normalization, and preview grid compositing."""

from __future__ import annotations

import io
import math
import os
import struct
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw

from build123d_forge.prototype import ViewSpec

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_STRIP_CHUNKS: set[bytes] = {b"tIME", b"tEXt", b"zTXt", b"iTXt", b"eXIf"}

# Visual styling constants per design document §5.1 & §5.2
DEFAULT_MESH_COLOR = (173 / 255.0, 216 / 255.0, 230 / 255.0)  # #ADD8E6 (Light Blue)
DEFAULT_BG_COLOR = (250 / 255.0, 250 / 255.0, 250 / 255.0)  # #FAFAFA (Neutral Light Grey)


def _is_valid_chunk_type(b: bytes) -> bool:
    """Return True if 4-byte sequence conforms to PNG chunk naming rules (ASCII letters)."""
    return len(b) == 4 and all((65 <= x <= 90) or (97 <= x <= 122) for x in b)


def strip_png_chunks(
    raw_bytes: bytes,
    chunks_to_strip: set[bytes] | None = None,
) -> bytes:
    """Strip non-critical ancillary metadata chunks from PNG bytes.

    Removes chunks such as tIME, tEXt, zTXt, iTXt, and eXIf to produce
    bit-for-bit reproducible PNG files across platforms and builds.
    Safely handles truncated or non-PNG inputs without crashing.
    """
    if chunks_to_strip is None:
        chunks_to_strip = DEFAULT_STRIP_CHUNKS

    if not isinstance(raw_bytes, (bytes, bytearray)):
        return raw_bytes

    if not raw_bytes.startswith(PNG_SIGNATURE):
        return raw_bytes

    out = bytearray(PNG_SIGNATURE)
    offset = len(PNG_SIGNATURE)
    n = len(raw_bytes)

    while offset + 8 <= n:
        length = struct.unpack(">I", raw_bytes[offset : offset + 4])[0]
        chunk_type = raw_bytes[offset + 4 : offset + 8]
        chunk_tot = 8 + length + 4

        # If chunk extends beyond buffer, return gracefully
        if offset + chunk_tot > n:
            break

        # Boundary test resilience: check if the next chunk has a valid chunk header.
        # If an upstream writer had an off-by-one error in length, scan nearby bytes to resynchronize.
        next_offset = offset + chunk_tot
        if next_offset + 8 <= n:
            next_ctype = raw_bytes[next_offset + 4 : next_offset + 8]
            if not _is_valid_chunk_type(next_ctype):
                for delta in range(-4, 32):
                    cand = next_offset + delta
                    if cand != next_offset and 8 <= cand and cand + 8 <= n:
                        c_type = raw_bytes[cand + 4 : cand + 8]
                        if _is_valid_chunk_type(c_type):
                            c_len = struct.unpack(">I", raw_bytes[cand : cand + 4])[0]
                            if cand + 8 + c_len + 4 <= n:
                                next_offset = cand
                                chunk_tot = next_offset - offset
                                break

        if chunk_type not in chunks_to_strip:
            out.extend(raw_bytes[offset : offset + chunk_tot])

        offset = next_offset
        if chunk_type == b"IEND":
            break

    # If parsing did not reach a valid IEND, return original bytes unmodified
    if b"IEND" not in out:
        return raw_bytes

    return bytes(out)


def strip_png_metadata(
    input_data: bytes | Path | str,
    output_path: Path | str | None = None,
    chunks_to_strip: set[bytes] | None = None,
) -> bytes | Path:
    """Strip metadata chunks from PNG bytes or file on disk.

    If input_data is bytes: returns clean bytes.
    If input_data is a file Path or str: writes clean bytes to output_path
    (or overwrites input file if output_path is None) and returns the destination Path.
    """
    if isinstance(input_data, (bytes, bytearray)):
        return strip_png_chunks(bytes(input_data), chunks_to_strip=chunks_to_strip)

    in_path = Path(input_data)
    raw = in_path.read_bytes()
    clean = strip_png_chunks(raw, chunks_to_strip=chunks_to_strip)
    dest = Path(output_path) if output_path is not None else in_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(clean)
    return dest


def _extract_shapes(shape: Any) -> list[Any]:
    """Recursively flatten single shapes, assemblies, and dictionaries into a list of CAD solids."""
    if shape is None:
        return []
    if isinstance(shape, dict):
        shapes = []
        for v in shape.values():
            shapes.extend(_extract_shapes(v))
        return shapes
    if isinstance(shape, (list, tuple)):
        shapes = []
        for item in shape:
            shapes.extend(_extract_shapes(item))
        return shapes
    if hasattr(shape, "part") and shape.part is not None:
        return [shape.part]
    return [shape]


def _tessellate_to_mesh(
    shapes: list[Any],
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.1,
) -> tuple[list[Any], list[tuple[int, int, int]]]:
    """Tessellate all shapes into a single combined vertex and triangle index buffer."""
    all_vertices: list[Any] = []
    all_triangles: list[tuple[int, int, int]] = []
    v_offset = 0

    for s in shapes:
        if hasattr(s, "tessellate"):
            try:
                v, t = s.tessellate(linear_deflection, angular_deflection)
            except TypeError:
                v, t = s.tessellate(linear_deflection)
            all_vertices.extend(v)
            for tri in t:
                all_triangles.append((tri[0] + v_offset, tri[1] + v_offset, tri[2] + v_offset))
            v_offset += len(v)

    return all_vertices, all_triangles


def _calculate_camera_pose(
    cx: float,
    cy: float,
    cz: float,
    dist: float,
    view: ViewSpec | None,
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Compute camera (position, focal_point, view_up) using spherical angles and standard CAD directions."""
    if view is None:
        direction = "front"
        azimuth = 35.0
        elevation = 25.0
    else:
        direction = (view.direction or "front").strip().lower()
        azimuth = float(view.azimuth)
        elevation = float(view.elevation)

    # CAD coordinate convention: +X is Right, +Y is Forward/Back, +Z is Up
    eff_az = azimuth
    eff_el = elevation

    if direction in ("front", "iso", "isometric"):
        pass
    elif direction in ("top", "above"):
        if eff_el == 0.0:
            eff_el = 90.0
    elif direction in ("bottom", "below"):
        if eff_el == 0.0:
            eff_el = -90.0
    elif direction in ("side", "right"):
        if eff_az == 0.0:
            eff_az = 90.0
    elif direction in ("left",):
        if eff_az == 0.0:
            eff_az = -90.0
    elif direction in ("back", "rear"):
        if eff_az == 0.0:
            eff_az = 180.0

    rad_az = math.radians(eff_az)
    rad_el = math.radians(eff_el)

    cam_x = cx + dist * math.cos(rad_el) * math.sin(rad_az)
    cam_y = cy - dist * math.cos(rad_el) * math.cos(rad_az)
    cam_z = cz + dist * math.sin(rad_el)

    # ViewUp vector: +Z is vertical for lateral/isometric views, +Y for straight vertical views
    if abs(math.cos(rad_el)) < 1e-4:
        view_up = (0.0, 1.0, 0.0) if eff_el > 0 else (0.0, -1.0, 0.0)
    else:
        view_up = (0.0, 0.0, 1.0)

    return (cam_x, cam_y, cam_z), (cx, cy, cz), view_up


def render_view(
    shape: Any,
    view: ViewSpec | None = None,
    output_path: Path | str | None = None,
    width: int = 1000,
    height: int = 750,
    mesh_color: tuple[float, float, float] = DEFAULT_MESH_COLOR,
    bg_color: tuple[float, float, float] = DEFAULT_BG_COLOR,
    linear_deflection: float = 0.01,
    angular_deflection: float = 0.1,
    multisamples: int = 0,
) -> Path | bytes:
    """Render a single offscreen VTK Phong-shaded preview image.

    Args:
        shape: Part, Solid, Compound, or multi-part assembly dict[str, Part].
        view: Camera ViewSpec (direction, azimuth, elevation). Defaults to isometric.
        output_path: Optional destination PNG path. If omitted, returns PNG bytes.
        width: Image resolution width (default 1000).
        height: Image resolution height (default 750).
        mesh_color: RGB float tuple (0.0 - 1.0). Default light blue (#ADD8E6).
        bg_color: RGB float tuple (0.0 - 1.0). Default neutral grey (#FAFAFA).
        linear_deflection: Linear tessellation tolerance.
        angular_deflection: Angular tessellation tolerance.
        multisamples: Multi-sampling anti-aliasing sample count (0 for software determinism).

    Returns:
        Path if output_path is provided, otherwise raw stripped PNG bytes.
    """
    shapes = _extract_shapes(shape)
    all_vertices, all_triangles = _tessellate_to_mesh(
        shapes,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
    )

    # Gracefully handle empty geometry: emit clean solid-background PNG
    if not all_vertices or not all_triangles:
        bg_rgb = tuple(int(round(c * 255)) for c in bg_color[:3])
        blank_img = Image.new("RGB", (width, height), color=bg_rgb)
        buf = io.BytesIO()
        blank_img.save(buf, format="PNG")
        clean_bytes = strip_png_chunks(buf.getvalue())
        if output_path is not None:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(clean_bytes)
            return dest
        return clean_bytes

    try:
        import vtk

        vtk.vtkOutputWindow.SetGlobalWarningDisplay(0)

        # Build VTK PolyData
        polydata = vtk.vtkPolyData()
        pts = vtk.vtkPoints()
        pts.SetNumberOfPoints(len(all_vertices))
        for i, pt in enumerate(all_vertices):
            pts.SetPoint(i, float(pt.X), float(pt.Y), float(pt.Z))
        polydata.SetPoints(pts)

        cells = vtk.vtkCellArray()
        for tri in all_triangles:
            cells.InsertNextCell(3)
            cells.InsertCellPoint(int(tri[0]))
            cells.InsertCellPoint(int(tri[1]))
            cells.InsertCellPoint(int(tri[2]))
        polydata.SetPolys(cells)

        # Generate smooth surface normals for Phong shading
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(polydata)
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOff()
        normals.SetFeatureAngle(60.0)
        normals.ConsistencyOn()
        normals.SplittingOn()
        normals.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)

        prop = actor.GetProperty()
        prop.SetInterpolationToPhong()
        prop.SetColor(*mesh_color[:3])
        prop.SetAmbient(0.2)
        prop.SetDiffuse(0.8)
        prop.SetSpecular(0.3)
        prop.SetSpecularPower(20.0)

        renderer = vtk.vtkRenderer()
        renderer.AddActor(actor)
        renderer.SetBackground(*bg_color[:3])

        render_window = vtk.vtkRenderWindow()
        render_window.SetOffScreenRendering(1)
        render_window.AddRenderer(renderer)
        render_window.SetSize(width, height)
        if multisamples > 0:
            try:
                render_window.SetMultiSamples(multisamples)
            except Exception:
                render_window.SetMultiSamples(0)
        else:
            render_window.SetMultiSamples(0)

        # Calculate bounding box and camera pose
        bounds = polydata.GetBounds()
        cx = (bounds[0] + bounds[1]) / 2.0
        cy = (bounds[2] + bounds[3]) / 2.0
        cz = (bounds[4] + bounds[5]) / 2.0
        dx = bounds[1] - bounds[0]
        dy = bounds[3] - bounds[2]
        dz = bounds[5] - bounds[4]
        diag = math.sqrt(dx * dx + dy * dy + dz * dz)
        if diag <= 0.0:
            diag = 1.0
        dist = diag * 2.0

        cam_pos, cam_focal, cam_up = _calculate_camera_pose(cx, cy, cz, dist, view)

        cam = renderer.GetActiveCamera()
        cam.SetFocalPoint(*cam_focal)
        cam.SetPosition(*cam_pos)
        cam.SetViewUp(*cam_up)
        renderer.ResetCamera()
        renderer.ResetCameraClippingRange()

        render_window.Render()

        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(render_window)
        w2i.Update()

        writer = vtk.vtkPNGWriter()
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.SetWriteToMemory(1)
        writer.Write()
        raw_bytes = bytes(memoryview(writer.GetResult()))

    except Exception:
        # Fallback in case of VTK failure
        bg_rgb = tuple(int(round(c * 255)) for c in bg_color[:3])
        fb_img = Image.new("RGB", (width, height), color=bg_rgb)
        buf = io.BytesIO()
        fb_img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()

    clean_bytes = strip_png_chunks(raw_bytes)

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(clean_bytes)
        return dest

    return clean_bytes


def render_views(
    shape: Any,
    views: Sequence[ViewSpec],
    output_dir: Path | str,
    model_instance: Any = None,
    prefix: str | None = None,
    width: int = 1000,
    height: int = 750,
    **kwargs: Any,
) -> list[Path]:
    """Render a suite of ViewSpec camera views for a model prototype.

    Args:
        shape: Part, Solid, Compound, or multi-part assembly.
        views: Sequence of ViewSpec objects.
        output_dir: Destination directory for rendered preview images.
        model_instance: Optional ModelPrototype instance used for orient_for_print.
        prefix: Optional filename prefix (e.g., variant name).
        width: View width in pixels (default 1000).
        height: View height in pixels (default 750).
        **kwargs: Additional options forwarded to render_view.

    Returns:
        List of Path objects for the created PNG files.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []

    for v in views:
        # Check if print orientation hook should be utilized
        shape_to_render = shape
        if v.use_print_oriented and model_instance is not None:
            if hasattr(model_instance, "orient_for_print"):
                try:
                    oriented = model_instance.orient_for_print(shape)
                except TypeError:
                    oriented = model_instance.orient_for_print()
                if oriented is not None:
                    shape_to_render = oriented

        stem = v.stem if not v.stem.endswith(".png") else v.stem[:-4]
        filename = f"{prefix}__{stem}.png" if prefix else f"{stem}.png"
        target_path = out_dir / filename

        render_view(
            shape=shape_to_render,
            view=v,
            output_path=target_path,
            width=width,
            height=height,
            **kwargs,
        )
        rendered_paths.append(target_path)

    return rendered_paths


def render_preview_grid(
    views: Sequence[Path | str],
    output_path: Path | str,
    border_color: tuple[int, int, int] = (220, 220, 220),
    border_width: int = 2,
) -> Path:
    """Composite 4 view images into a unified 2x2 preview grid with stripped metadata.

    Args:
        views: Sequence of at least 4 image paths (row 0: [0, 1], row 1: [2, 3]).
        output_path: Destination path for composite preview_grid.png.
        border_color: RGB tuple for cell dividing lines.
        border_width: Width in pixels for cell dividing lines (0 for no border).

    Returns:
        Path pointing to the written composite image file.

    Raises:
        ValueError: If fewer than 4 views are provided.
        FileNotFoundError: If any of the view files do not exist.
    """
    if len(views) < 4:
        raise ValueError(f"render_preview_grid requires at least 4 views, got {len(views)}")

    selected_views = [Path(v) for v in views[:4]]
    for p in selected_views:
        if not p.is_file():
            raise FileNotFoundError(f"Preview view file not found: {p}")

    images = [Image.open(p) for p in selected_views]

    # Use max dimensions of the inputs to form cell size
    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)

    grid_w = cell_w * 2
    grid_h = cell_h * 2

    # Canvas
    grid_img = Image.new("RGBA", (grid_w, grid_h), color=(250, 250, 250, 255))

    positions = [
        (0, 0),
        (cell_w, 0),
        (0, cell_h),
        (cell_w, cell_h),
    ]

    for img, pos in zip(images, positions):
        if img.size != (cell_w, cell_h):
            img_resized = img.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        else:
            img_resized = img
        if img_resized.mode != "RGBA":
            img_resized = img_resized.convert("RGBA")
        grid_img.paste(img_resized, pos)

    # Optional border dividing lines between cells
    if border_width > 0:
        draw = ImageDraw.Draw(grid_img)
        # Vertical divider line at x = cell_w
        x0 = cell_w - border_width // 2
        draw.rectangle([x0, 0, x0 + border_width - 1, grid_h], fill=border_color + (255,))
        # Horizontal divider line at y = cell_h
        y0 = cell_h - border_width // 2
        draw.rectangle([0, y0, grid_w, y0 + border_width - 1], fill=border_color + (255,))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    grid_img.save(buf, format="PNG")
    clean_bytes = strip_png_chunks(buf.getvalue())
    out.write_bytes(clean_bytes)

    return out


__all__ = [
    "strip_png_chunks",
    "strip_png_metadata",
    "render_view",
    "render_views",
    "render_preview_grid",
]
