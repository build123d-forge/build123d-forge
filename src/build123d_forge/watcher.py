"""Lightweight standard-library file watcher for build123d-forge models and configs."""

from __future__ import annotations

import datetime
from pathlib import Path
import time
from typing import Any, Callable, Sequence


def get_watched_snapshot(dirs: Sequence[Path | str]) -> dict[Path, tuple[float, int]]:
    """Scan directories and build a snapshot of file modification times and sizes."""
    snapshot: dict[Path, tuple[float, int]] = {}
    ignored_parts = {".venv", "venv", "__pycache__", ".git", "dist", ".agents", ".pytest_cache"}
    for d in dirs:
        dir_path = Path(d)
        if not dir_path.is_dir():
            continue
        for p in dir_path.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".py", ".toml"}:
                if any(part in p.parts for part in ignored_parts):
                    continue
                try:
                    stat = p.stat()
                    snapshot[p.resolve()] = (stat.st_mtime, stat.st_size)
                except OSError:
                    pass
    return snapshot


def detect_file_changes(
    prev_snapshot: dict[Path, tuple[float, int]],
    curr_snapshot: dict[Path, tuple[float, int]],
) -> list[Path]:
    """Identify added, removed, or modified files between snapshots."""
    changes: list[Path] = []
    # Check modified or added
    for path, stat in curr_snapshot.items():
        if path not in prev_snapshot or prev_snapshot[path] != stat:
            changes.append(path)
    # Check removed
    for path in prev_snapshot:
        if path not in curr_snapshot:
            changes.append(path)
    return sorted(changes)


def watch_and_rebuild(
    root_dir: Path | str | None = None,
    targets: Sequence[str] | None = None,
    poll_interval: float = 1.0,
    once: bool = False,
    max_polls: int | None = None,
    build_callback: Callable[[Sequence[str] | None], Any] | None = None,
) -> None:
    """Poll watched directories and trigger rebuilds upon detected file changes.

    Args:
        root_dir: Project root directory to watch. Defaults to cwd.
        targets: Optional target list to rebuild.
        poll_interval: Polling frequency in seconds.
        once: If True, executes a single build check and returns immediately.
        max_polls: Optional maximum polling iterations before returning.
        build_callback: Optional custom callback function(targets) to trigger builds.
    """
    root = Path(root_dir or Path.cwd()).resolve()
    watch_candidates = [root / "models", root / "configs", root / "parts"]
    watch_dirs = [d for d in watch_candidates if d.is_dir()]
    if not watch_dirs:
        watch_dirs = [root]

    if build_callback is None:
        from build123d_forge.builder import build_project

        build_callback = lambda t: build_project(targets=t, project_root=root)

    print(f"Starting bforge watch on: {', '.join(str(d) for d in watch_dirs)}")
    try:
        build_callback(targets)
    except Exception as err:
        print(f"Initial build status: {err}")

    if once:
        return

    snapshot = get_watched_snapshot(watch_dirs)
    iteration = 0

    try:
        while True:
            if max_polls is not None and iteration >= max_polls:
                break

            time.sleep(poll_interval)
            iteration += 1

            current = get_watched_snapshot(watch_dirs)
            changes = detect_file_changes(snapshot, current)
            if changes:
                now_str = datetime.datetime.now().strftime("%H:%M:%S")
                changed_names = [p.name for p in changes]
                print(f"[{now_str}] Changes detected in: {', '.join(changed_names)}. Rebuilding...")
                try:
                    build_callback(targets)
                except Exception as err:
                    print(f"[{now_str}] Rebuild failed: {err}")
                snapshot = current
    except KeyboardInterrupt:
        print("\nWatcher stopped.")
