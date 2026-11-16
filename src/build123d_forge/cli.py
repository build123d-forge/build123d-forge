"""build123d-forge CLI: Command-line interface for parametric build123d release engineering."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tomllib
from typing import Any

import click

from build123d_forge import __version__
from build123d_forge.agents import (
    get_agents_directive,
    scaffold_agents,
)
from build123d_forge.builder import (
    build_project,
    check_project,
    clean_dist,
    discover_configs,
    discover_models,
    discover_variants,
    list_project,
    to_snake_case,
)
from build123d_forge.cache import find_project_root
from build123d_forge.config import scaffold_config
from build123d_forge.prototype import ModelPrototype
from build123d_forge.release import package_release
from build123d_forge.schema import (
    export_json_schema,
    generate_json_schema,
    serialize_json_schema,
)
from build123d_forge.watcher import watch_and_rebuild


# ==============================================================================
# Top-Level CLI Command Group
# ==============================================================================

@click.group(
    name="bforge",
    context_settings=dict(help_option_names=["-h", "--help"]),
)
@click.version_option(
    version=__version__,
    prog_name="bforge",
    message="%(prog)s version %(version)s",
)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """build123d-forge: Declarative configurations & reproducible CAD releases for build123d."""
    ctx.ensure_object(dict)
    ctx.obj["project_root"] = find_project_root(Path.cwd()).resolve()


main = cli


# ==============================================================================
# Subcommand: clean
# ==============================================================================

@cli.command("clean")
@click.option(
    "-d", "--dist",
    default=None,
    type=click.Path(path_type=Path),
    help="Output directory to clean (default: dist/).",
)
@click.pass_context
def clean_cmd(ctx: click.Context, dist: Path | None) -> None:
    """Remove generated dist/ build artifacts and cache manifests."""
    root: Path = ctx.obj["project_root"]
    dist_path = dist or (root / "dist")
    if not dist_path.is_absolute():
        dist_path = root / dist_path

    if clean_dist(dist_path, project_root=root):
        click.echo(f"Cleaned {dist_path}")
    else:
        click.echo(f"{dist_path} does not exist. Nothing to clean.")


# ==============================================================================
# Subcommand: new-config
# ==============================================================================

@cli.command("new-config")
@click.argument("name", required=True)
@click.option("-m", "--model", default=None, help="ModelPrototype class name to scaffold.")
@click.option("-o", "--out", type=click.Path(path_type=Path), default=None, help="Output destination path.")
@click.option("-f", "--force", is_flag=True, default=False, help="Overwrite configuration file if it already exists.")
@click.option("-c", "--configs-dir", default="configs", help="Directory to store configurations.")
@click.option("--schema-url", default=None, help="Schema URL for #:schema directive.")
@click.pass_context
def new_config_cmd(
    ctx: click.Context,
    name: str,
    model: str | None,
    out: Path | None,
    force: bool,
    configs_dir: str,
    schema_url: str | None,
) -> None:
    """Create a new sparse .toml configuration preset pre-populated with documentation and defaults."""
    root: Path = ctx.obj["project_root"]
    models = discover_models(project_root=root)

    if not models:
        raise click.ClickException("No ModelPrototype models found in project to scaffold from.")

    # Resolve target model class
    model_cls: type[ModelPrototype] | None = None
    if model:
        if model in models:
            model_cls = models[model]
        else:
            matches = [m for m in models.values() if m.__name__.lower() == model.lower()]
            if matches:
                model_cls = matches[0]
            else:
                raise click.ClickException(f"Specified model '{model}' not found. Available models: {list(models.keys())}")
    elif len(models) == 1:
        model_cls = next(iter(models.values()))
    elif name in models:
        model_cls = models[name]
    else:
        matching = [m for m in models.values() if m.__name__.lower() == name.lower()]
        if matching:
            model_cls = matching[0]
        else:
            raise click.ClickException(
                f"Multiple models found ({list(models.keys())}). Please specify target model via --model."
            )

    # Determine destination path
    if out:
        target_path = out if out.is_absolute() else (root / out)
    else:
        cfg_dir = root / configs_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        filename = name if name.endswith(".toml") else f"{name}.toml"
        target_path = cfg_dir / filename

    if target_path.exists() and not force:
        raise click.ClickException(f"Configuration file '{target_path}' already exists. Use --force to overwrite.")

    try:
        url = schema_url or f"../dist/schemas/{model_cls.__name__}.schema.json"
        written = scaffold_config(model_cls, destination_path=target_path, force=force, schema_url=url)
        click.echo(f"Created configuration template at {written}")
    except FileExistsError as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Failed to generate configuration template: {exc}")


# ==============================================================================
# Subcommand: init-agents
# ==============================================================================

@cli.command("init-agents")
@click.option(
    "-o", "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Output destination file or directory (default: AGENTS.md in project root).",
)
@click.option(
    "-f", "--force",
    is_flag=True,
    default=False,
    help="Overwrite destination file if it already exists.",
)
@click.option(
    "--stdout",
    is_flag=True,
    default=False,
    help="Print AGENTS.md directive directly to stdout.",
)
@click.pass_context
def init_agents_cmd(
    ctx: click.Context,
    out: Path | None,
    force: bool,
    stdout: bool,
) -> None:
    """Scaffold a canonical AGENTS.md directive delegating CAD builds and previews to bforge."""
    if stdout:
        click.echo(get_agents_directive().rstrip("\n"))
        return

    root: Path = ctx.obj["project_root"]
    if out:
        target_path = out if out.is_absolute() else (root / out)
    else:
        target_path = root / "AGENTS.md"

    try:
        written = scaffold_agents(destination_path=target_path, force=force)
        click.echo(f"Created agent directive at {written}")
    except FileExistsError as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Failed to write agent directive: {exc}")


# ==============================================================================
# Subcommand: schema
# ==============================================================================

@cli.command("schema")
@click.argument("model", required=False, default=None)
@click.option("-o", "--out", type=click.Path(path_type=Path), default=None, help="Output destination file or dir.")
@click.option("--stdout", is_flag=True, default=False, help="Print JSON Schema directly to stdout.")
@click.option("--indent", default=2, type=int, help="Indentation spaces for JSON output.")
@click.option("--directives/--no-directives", default=True, help="Include extends and model directives.")
@click.pass_context
def schema_cmd(
    ctx: click.Context,
    model: str | None,
    out: Path | None,
    stdout: bool,
    indent: int,
    directives: bool,
) -> None:
    """Export Draft 2020-12 JSON Schema for IDE autocomplete and validation."""
    root: Path = ctx.obj["project_root"]
    models = discover_models(project_root=root)

    if not models:
        raise click.ClickException("No ModelPrototype models found in project.")

    target_models: list[type[ModelPrototype]] = []
    if model:
        if model in models:
            target_models.append(models[model])
        else:
            matches = [m for m in models.values() if m.__name__.lower() == model.lower()]
            if matches:
                target_models.append(matches[0])
            else:
                raise click.ClickException(f"Model '{model}' not found. Available models: {list(models.keys())}")
    else:
        target_models = list(models.values())

    if stdout:
        for m_cls in target_models:
            schema_dict = generate_json_schema(m_cls, include_directives=directives)
            click.echo(serialize_json_schema(schema_dict, indent=indent))
        return

    out_base = out or (root / "dist" / "schemas")
    if not out_base.is_absolute():
        out_base = root / out_base

    for m_cls in target_models:
        if out and out.suffix.lower() == ".json":
            target_file = out_base
        else:
            target_file = out_base / f"{m_cls.__name__}.schema.json"

        written = export_json_schema(m_cls, target_file, indent=indent, include_directives=directives)
        click.echo(f"Exported schema: {written}")


# ==============================================================================
# Subcommand: list
# ==============================================================================

@cli.command("list")
@click.option("-d", "--dist", default="dist", type=click.Path(path_type=Path), help="Dist directory.")
@click.option("--models-dir", default=None, help="Directory containing model sources.")
@click.option("--configs-dir", default=None, help="Directory containing configs.")
@click.pass_context
def list_cmd(
    ctx: click.Context,
    dist: Path,
    models_dir: str | None,
    configs_dir: str | None,
) -> None:
    """List discovered models, configuration presets, and cache status."""
    root: Path = ctx.obj["project_root"]
    dist_path = dist if dist.is_absolute() else (root / dist)
    search_dirs = [Path(models_dir)] if models_dir else None
    models = discover_models(search_dirs=search_dirs, project_root=root)
    statuses = list_project(
        dist_dir=dist_path,
        project_root=root,
        models_dir=models_dir,
        configs_dir=configs_dir,
    )

    click.echo("=" * 70)
    click.echo(f"build123d-forge Project: {root.name}")
    click.echo("=" * 70)

    click.echo("\nDiscovered Models:")
    if not models:
        click.echo("  (none found)")
    else:
        for name, cls in sorted(models.items()):
            src = getattr(cls, "_source_file", "(unknown)")
            try:
                rel_src = Path(src).relative_to(root)
            except Exception:
                rel_src = src
            click.echo(f"  • {name}  [{rel_src}]")

    click.echo("\nConfiguration Presets & Cache Status:")
    if not statuses:
        click.echo("  (none found)")
    else:
        header = f"  {'VARIANT':<20} {'MODEL':<20} {'STATUS':<15} {'CONFIG FILE'}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for s in statuses:
            click.echo(f"  {s.variant_name:<20} {s.model_name:<20} {s.status:<15} {s.config_file}")
    click.echo()


# ==============================================================================
# Subcommand: check
# ==============================================================================

@cli.command("check")
@click.argument("targets", nargs=-1)
@click.option("--strict", is_flag=True, default=False, help="Strict checking mode.")
@click.option("--models-dir", default=None, help="Directory containing model sources.")
@click.option("--configs-dir", default=None, help="Directory containing configs.")
@click.pass_context
def check_cmd(
    ctx: click.Context,
    targets: tuple[str, ...],
    strict: bool,
    models_dir: str | None,
    configs_dir: str | None,
) -> None:
    """Validate configs with Pydantic and verify B-Rep watertightness gates."""
    root: Path = ctx.obj["project_root"]
    try:
        results = check_project(
            targets=list(targets) if targets else None,
            strict=strict,
            project_root=root,
            models_dir=models_dir,
            configs_dir=configs_dir,
        )
    except Exception as exc:
        raise click.ClickException(str(exc))

    failed = False
    for r in results:
        if r.success:
            vol_str = f", volume: {r.report['volume_mm3']:.2f} mm³" if r.report and "volume_mm3" in r.report else ""
            click.echo(f"  ✓ {r.variant} ({r.model}): Valid watertight manifold solid{vol_str}")
        else:
            failed = True
            click.echo(f"  ✗ {r.variant} ({r.model}): Validation failed", err=True)
            for err in r.errors:
                click.echo(f"    • {err}", err=True)
                # Also echo to stdout for test harness compatibility
                click.echo(f"    • {err}")

    if failed:
        sys.exit(1)

    click.echo("\nAll model configurations passed validation gates.")


# ==============================================================================
# Subcommand: build
# ==============================================================================

@cli.command("build")
@click.argument("targets", nargs=-1)
@click.option("-f", "--force", is_flag=True, default=False, help="Force rebuild bypassing cache.")
@click.option("-j", "--workers", "--jobs", "workers", type=int, default=1, help="Parallel worker count.")
@click.option("-o", "--output-dir", default="dist", type=click.Path(path_type=Path), help="Output directory.")
@click.option("--linear-deflection", default=0.01, type=float, help="Linear deflection for STL meshing.")
@click.option("--angular-deflection", default=0.05, type=float, help="Angular deflection for STL meshing.")
@click.option("--epoch", default=None, type=int, help="Override epoch timestamp for STEP export.")
@click.option("--models-dir", default=None, help="Directory containing model sources.")
@click.option("--configs-dir", default=None, help="Directory containing configs.")
@click.pass_context
def build_cmd(
    ctx: click.Context,
    targets: tuple[str, ...],
    force: bool,
    workers: int,
    output_dir: Path,
    linear_deflection: float,
    angular_deflection: float,
    epoch: int | None,
    models_dir: str | None,
    configs_dir: str | None,
) -> None:
    """Incrementally build model configurations in parallel into dist/<variant>/."""
    root: Path = ctx.obj["project_root"]
    dist_dir = output_dir if output_dir.is_absolute() else (root / output_dir)

    try:
        results = build_project(
            targets=list(targets) if targets else None,
            output_dir=dist_dir,
            force=force,
            workers=workers,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
            epoch=epoch,
            project_root=root,
            models_dir=models_dir,
            configs_dir=configs_dir,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Build failed: {exc}")

    failed = False
    for r in results:
        if r.success:
            tag = "[cached]" if r.cached else "[built]"
            click.echo(f"  {tag:<8} {r.variant} ({r.model})")
        else:
            failed = True
            click.echo(f"  [error]  {r.variant}: {r.error}", err=True)

    if failed:
        sys.exit(1)


# ==============================================================================
# Subcommand: release-package
# ==============================================================================

@cli.command("release-package")
@click.option("-t", "--tag", required=True, help="Release tag version (e.g. v1.0.0).")
@click.option("-o", "--out", default="release_assets", type=click.Path(path_type=Path), help="Asset bundle output directory.")
@click.option("-d", "--dist", default="dist", type=click.Path(path_type=Path), help="Source dist directory.")
@click.option("-r", "--repo", default=None, help="Repository or project name prefix for zip archives.")
@click.pass_context
def release_package_cmd(
    ctx: click.Context,
    tag: str,
    out: Path,
    dist: Path,
    repo: str | None,
) -> None:
    """Bundle per-variant .zip archives, flat assets, SHA256SUMS.txt, and RELEASE_NOTES.md."""
    root: Path = ctx.obj["project_root"]
    dist_dir = dist if dist.is_absolute() else (root / dist)
    out_dir = out if out.is_absolute() else (root / out)

    if not tag or not tag.strip():
        raise click.BadParameter("Release tag cannot be empty.", param_hint="--tag")

    try:
        out_path = package_release(dist_dir=dist_dir, out_dir=out_dir, tag=tag.strip(), repo_name=repo)
        click.echo(f"Release package generated successfully in {out_path}")
    except Exception as exc:
        raise click.ClickException(str(exc))


# ==============================================================================
# Subcommand: watch
# ==============================================================================

@cli.command("watch")
@click.argument("targets", nargs=-1)
@click.option("-i", "--interval", default=1.0, type=float, help="Polling interval in seconds.")
@click.option("--once", is_flag=True, default=False, help="Run a single build cycle and exit.")
@click.option("--poll-limit", "--max-polls", "poll_limit", default=None, type=int, help="Limit number of polling iterations.")
@click.option("-o", "--output-dir", default="dist", type=click.Path(path_type=Path), help="Output directory.")
@click.pass_context
def watch_cmd(
    ctx: click.Context,
    targets: tuple[str, ...],
    interval: float,
    once: bool,
    poll_limit: int | None,
    output_dir: Path,
) -> None:
    """Watch models and configs, incrementally rebuilding changed presets on save."""
    root: Path = ctx.obj["project_root"]
    dist_dir = output_dir if output_dir.is_absolute() else (root / output_dir)

    def custom_build(t: Any) -> Any:
        return build_project(targets=t, output_dir=dist_dir, project_root=root)

    try:
        watch_and_rebuild(
            root_dir=root,
            targets=list(targets) if targets else None,
            poll_interval=interval,
            once=once,
            max_polls=poll_limit,
            build_callback=custom_build,
        )
    except KeyboardInterrupt:
        click.echo("\nWatcher stopped.")
    except Exception as exc:
        raise click.ClickException(str(exc))
