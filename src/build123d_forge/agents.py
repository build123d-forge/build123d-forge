"""Canonical AGENTS.md directive template and scaffolding helpers for consumer CAD projects."""

from __future__ import annotations

from pathlib import Path

AGENTS_TEMPLATE_PATH: Path = Path(__file__).resolve().parent / "templates" / "AGENTS.template.md"


def get_agents_directive() -> str:
    """Return the canonical consumer AGENTS.md directive Markdown text."""
    return AGENTS_TEMPLATE_PATH.read_text(encoding="utf-8")


def scaffold_agents(
    destination_path: Path | str = "AGENTS.md",
    *,
    force: bool = False,
) -> Path:
    """Write the canonical consumer AGENTS.md directive to destination_path.

    Args:
        destination_path: Target file path (or existing directory, in which case
            ``AGENTS.md`` is written inside that directory).
        force: If True, overwrite ``destination_path`` if it already exists.

    Returns:
        Resolved ``Path`` pointing to the written directive file.

    Raises:
        FileExistsError: If the target file already exists and ``force`` is False.
    """
    dest = Path(destination_path)
    if dest.exists() and dest.is_dir():
        dest = dest / "AGENTS.md"

    if dest.exists() and not force:
        raise FileExistsError(
            f"Agent directive '{dest}' already exists. Use --force to overwrite."
        )

    content = get_agents_directive()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest
