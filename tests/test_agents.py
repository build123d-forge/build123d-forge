"""Unit tests for consumer AGENTS.md directive template, CLI conformance, and scaffolding."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import tempfile
import tomllib
import unittest

import click

from build123d_forge.agents import (
    AGENTS_TEMPLATE_PATH,
    get_agents_directive,
    scaffold_agents,
)
from build123d_forge.cli import cli
from build123d_forge.prototype import ModelPrototype


def _extract_fenced_blocks(markdown: str, lang: str) -> list[str]:
    """Extract fenced code blocks (including indented list blocks) for a language tag."""
    pattern = re.compile(rf"^[ \t]*```{re.escape(lang)}\n(.*?)\n[ \t]*```", re.MULTILINE | re.DOTALL)
    return pattern.findall(markdown)


class TestAgentsDirective(unittest.TestCase):
    """Verify template structure, runnable examples, CLI flag conformance, and scaffolding."""

    def test_template_exists_and_fits_rule_budget(self) -> None:
        """Template file exists, starts without YAML frontmatter, and stays well under 24 KB."""
        self.assertTrue(AGENTS_TEMPLATE_PATH.is_file())
        content = get_agents_directive()
        self.assertTrue(content.startswith("# AGENTS.md"))
        self.assertFalse(content.startswith("---"))
        encoded = content.encode("utf-8")
        self.assertGreater(len(encoded), 1000)
        self.assertLess(len(encoded), 24_000)

    def test_directive_prescribes_delegation_and_guardrails(self) -> None:
        """Directive instructs agents to edit Python/TOML and delegate builds/previews to bforge."""
        content = get_agents_directive()
        required_terms = [
            "ModelPrototype",
            "Param(",
            "ViewSpec(",
            "orient_for_print",
            "mcp_shim(",
            "configs/",
            "models/",
            "dist/",
            "preview_grid.png",
            "manifest.json",
            "bforge list",
            "bforge check",
            "bforge build",
            "bforge new-config",
            "bforge schema",
            "bforge watch",
            "bforge build --force",
            "releases/latest/download/",
            "azimuth == 0",
            "VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow",
            "build123d-mcp",
        ]
        for term in required_terms:
            self.assertIn(term, content, f"Expected '{term}' in AGENTS directive")

    def test_embedded_python_prototype_example_is_runnable_and_valid(self) -> None:
        """The canonical ModelPrototype example in AGENTS.template.md executes and passes B-Rep gates."""
        py_blocks = _extract_fenced_blocks(get_agents_directive(), "python")
        self.assertGreaterEqual(len(py_blocks), 1)

        namespace: dict[str, object] = {"__name__": "test_agents_example"}
        exec(py_blocks[0], namespace)

        bracket_cls = namespace.get("MountingBracket")
        self.assertIsNotNone(bracket_cls)
        assert isinstance(bracket_cls, type)
        self.assertTrue(issubclass(bracket_cls, ModelPrototype))

        # Verify mcp_shim populated PART_INFO, make, and make_print_oriented
        self.assertIn("PART_INFO", namespace)
        self.assertTrue(callable(namespace.get("make")))
        self.assertTrue(callable(namespace.get("make_print_oriented")))

        # Verify prototype builds a watertight manifold B-Rep and 4-view camera suite
        instance = bracket_cls()
        part = instance.build()
        report = instance.verify(part)
        self.assertTrue(report.is_valid, f"Validation failed: {report.errors}")
        self.assertTrue(report.is_manifold)
        self.assertTrue(report.watertight)
        self.assertGreater(report.volume_mm3, 0.0)
        self.assertGreaterEqual(len(instance.views()), 4)

    def test_embedded_toml_example_parses_cleanly(self) -> None:
        """The sparse TOML example in AGENTS.template.md is valid stdlib tomllib TOML."""
        toml_blocks = _extract_fenced_blocks(get_agents_directive(), "toml")
        self.assertGreaterEqual(len(toml_blocks), 1)
        parsed = tomllib.loads(toml_blocks[0])
        self.assertEqual(parsed.get("extends"), "default.toml")
        self.assertIn("width", parsed)

    def test_embedded_bforge_bash_commands_conform_to_click_cli(self) -> None:
        """Every bforge command in bash blocks matches live Click subcommands and options."""
        bash_blocks = _extract_fenced_blocks(get_agents_directive(), "bash")
        self.assertGreaterEqual(len(bash_blocks), 1)

        checked = 0
        for block in bash_blocks:
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens = shlex.split(line, comments=True)
                for idx, tok in enumerate(tokens):
                    if tok in ("bforge", "build123d-forge"):
                        checked += 1
                        subargs = tokens[idx + 1 :]
                        self.assertTrue(subargs, f"Expected subcommand in '{line}'")
                        subcmd = subargs[0]
                        self.assertIn(subcmd, cli.commands, f"Unknown subcommand '{subcmd}' in '{line}'")
                        cmd = cli.commands[subcmd]
                        allowed = {"--help", "-h"}
                        for p in cmd.params:
                            if isinstance(p, click.Option):
                                allowed.update(p.opts)
                                allowed.update(p.secondary_opts)
                        for arg in subargs[1:]:
                            if arg.startswith("-"):
                                opt = arg.split("=", 1)[0]
                                self.assertIn(opt, allowed, f"Unknown option '{opt}' for '{subcmd}' in '{line}'")
        self.assertGreaterEqual(checked, 4)

    def test_scaffold_agents_writes_guards_and_handles_directories(self) -> None:
        """scaffold_agents writes AGENTS.md, respects force, and resolves directory targets."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. Passing an existing directory writes <dir>/AGENTS.md
            written = scaffold_agents(root)
            self.assertEqual(written, root / "AGENTS.md")
            self.assertEqual(written.read_text(encoding="utf-8"), get_agents_directive())

            # 2. Refuses overwrite when force=False
            with self.assertRaises(FileExistsError):
                scaffold_agents(written, force=False)

            # 3. Overwrites when force=True
            written.write_text("custom", encoding="utf-8")
            scaffold_agents(written, force=True)
            self.assertEqual(written.read_text(encoding="utf-8"), get_agents_directive())

            # 4. Creates nested parent directories for custom file paths
            nested = root / "sub" / "dir" / "GEMINI.md"
            written_nested = scaffold_agents(nested)
            self.assertEqual(written_nested, nested)
            self.assertTrue(nested.is_file())


if __name__ == "__main__":
    unittest.main()
