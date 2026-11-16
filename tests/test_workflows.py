"""
Unit tests for GitHub Actions workflows in build123d-forge.

This test module verifies the existence, syntactic validity, structure,
CLI option conformance, and required steps of .github/workflows/ci.yml and
.github/workflows/build-and-release.yml.
It runs completely offline with standard library unittest and no external YAML dependencies.
"""

from __future__ import annotations

import os
import re
import shlex
import unittest
from pathlib import Path

import click

from build123d_forge.cli import cli


# ---------------------------------------------------------------------------
# Path Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW_PATH = WORKFLOWS_DIR / "ci.yml"
RELEASE_WORKFLOW_PATH = WORKFLOWS_DIR / "build-and-release.yml"


# ---------------------------------------------------------------------------
# Zero-dependency Lightweight YAML Parser for GitHub Actions Workflows
# ---------------------------------------------------------------------------

def _strip_inline_comment(val: str) -> str:
    """Strip trailing unquoted comments (# ...) from a scalar line."""
    in_single = False
    in_double = False
    escape = False
    for i, ch in enumerate(val):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_double:
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or val[i - 1].isspace():
                return val[:i].rstrip()
    return val


def _split_mapping_key_value(line: str) -> tuple[str, str] | None:
    """Find unquoted ':' followed by space or end-of-string to split mapping key and value."""
    in_single = False
    in_double = False
    escape = False
    for i, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_double:
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(line) or line[i + 1].isspace():
                return line[:i].strip(), line[i + 1:].strip()
    return None


def _parse_scalar(val: str):
    """Parse scalar types: booleans, ints, floats, lists, and unquoted strings."""
    val = _strip_inline_comment(val).strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"') and len(val) >= 2) or \
       (val.startswith("'") and val.endswith("'") and len(val) >= 2):
        return val[1:-1]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        items = []
        for x in inner.split(","):
            x = _strip_inline_comment(x).strip()
            if (x.startswith('"') and x.endswith('"') and len(x) >= 2) or \
               (x.startswith("'") and x.endswith("'") and len(x) >= 2):
                x = x[1:-1]
            items.append(x)
        return items
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return val


def _parse_block_scalar(lines: list[str], start_idx: int, min_indent: int) -> tuple[str, int]:
    """Parse a block scalar (| or >) preserving internal relative indentation."""
    block_lines = []
    content_indent = None
    i = start_idx
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            if content_indent is not None:
                block_lines.append("")
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= min_indent:
            break
        if content_indent is None:
            content_indent = indent
        if indent >= content_indent:
            block_lines.append(raw[content_indent:].rstrip())
        else:
            block_lines.append(raw.strip())
        i += 1
    while block_lines and not block_lines[-1]:
        block_lines.pop()
    return "\n".join(block_lines), i


def _parse_yaml_lines(lines: list[str], start_idx: int = 0, base_indent: int = 0):
    """Recursive-descent parser for indentation-based YAML subset used in GitHub Actions."""
    i = start_idx
    is_list = None
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent < base_indent:
            break
        if stripped.startswith("- "):
            is_list = True
        else:
            is_list = False
        break

    if is_list is None:
        return {}, i

    if is_list:
        res = []
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent < base_indent:
                break
            if stripped.startswith("- "):
                sub_text = stripped[2:].strip()
                item_indent = indent + 2
                clean_sub = _strip_inline_comment(sub_text).strip()
                pair = _split_mapping_key_value(clean_sub)
                if pair is not None:
                    k, v = pair
                    item_dict = {}
                    if v in ("|", ">", "|-", ">-", "|+", ">+"):
                        item_dict[k], i = _parse_block_scalar(lines, i + 1, indent)
                    elif v == "":
                        sub_obj, i = _parse_yaml_lines(lines, i + 1, item_indent + 2)
                        item_dict[k] = sub_obj
                    else:
                        item_dict[k] = _parse_scalar(v)
                        i += 1

                    while i < len(lines):
                        nxt_raw = lines[i]
                        nxt_stripped = nxt_raw.strip()
                        if not nxt_stripped or nxt_stripped.startswith("#"):
                            i += 1
                            continue
                        nxt_indent = len(nxt_raw) - len(nxt_raw.lstrip(" "))
                        if nxt_indent < item_indent:
                            break
                        if nxt_indent >= item_indent and not nxt_stripped.startswith("- "):
                            clean_nxt = _strip_inline_comment(nxt_stripped).strip()
                            pair_nxt = _split_mapping_key_value(clean_nxt)
                            if pair_nxt is not None:
                                nk, nv = pair_nxt
                                if nv in ("|", ">", "|-", ">-", "|+", ">+"):
                                    item_dict[nk], i = _parse_block_scalar(lines, i + 1, item_indent)
                                elif nv == "":
                                    sub_obj, i = _parse_yaml_lines(lines, i + 1, nxt_indent + 2)
                                    item_dict[nk] = sub_obj
                                else:
                                    item_dict[nk] = _parse_scalar(nv)
                                    i += 1
                            else:
                                i += 1
                        else:
                            break
                    res.append(item_dict)
                else:
                    res.append(_parse_scalar(sub_text))
                    i += 1
            else:
                break
        return res, i
    else:
        res = {}
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if indent < base_indent:
                break
            clean_str = _strip_inline_comment(stripped).strip()
            pair = _split_mapping_key_value(clean_str)
            if pair is not None:
                k, v = pair
                if v in ("|", ">", "|-", ">-", "|+", ">+"):
                    res[k], i = _parse_block_scalar(lines, i + 1, indent)
                elif v == "":
                    sub_obj, i = _parse_yaml_lines(lines, i + 1, indent + 2)
                    res[k] = sub_obj
                else:
                    res[k] = _parse_scalar(v)
                    i += 1
            else:
                i += 1
        return res, i


def load_yaml(text: str) -> dict:
    """Load YAML string using PyYAML if installed, otherwise falling back to lightweight parser."""
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        data, _ = _parse_yaml_lines(text.splitlines(), 0, 0)
        return data


def validate_yaml_syntax_and_indentation(text: str) -> list[str]:
    """Validate common YAML syntax rules such as tab characters and colon formatting."""
    errors = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        if "\t" in line:
            errors.append(f"Line {idx}: contains literal TAB character (YAML requires spaces)")
    return errors


# ---------------------------------------------------------------------------
# Reference Specifications
# ---------------------------------------------------------------------------

CONSUMER_RELEASE_YAML_SAMPLE = """# .github/workflows/release.yml
name: Build & Release Models

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:

jobs:
  models:
    uses: build123d-forge/build123d-forge/.github/workflows/build-and-release.yml@main
    permissions:
      contents: write
"""


# ---------------------------------------------------------------------------
# Test Suites: File Presence and Syntax
# ---------------------------------------------------------------------------

class TestWorkflowFilePresence(unittest.TestCase):
    """Verify that workflow files exist and satisfy basic formatting constraints."""

    def test_workflow_directory_exists(self):
        self.assertTrue(
            WORKFLOWS_DIR.is_dir(),
            f"Expected workflows directory at {WORKFLOWS_DIR}"
        )

    def test_ci_workflow_file_exists(self):
        self.assertTrue(
            CI_WORKFLOW_PATH.is_file(),
            f"Expected CI workflow at {CI_WORKFLOW_PATH}"
        )

    def test_build_and_release_workflow_file_exists(self):
        self.assertTrue(
            RELEASE_WORKFLOW_PATH.is_file(),
            f"Expected release workflow at {RELEASE_WORKFLOW_PATH}"
        )

    def test_ci_workflow_no_tab_characters(self):
        if not CI_WORKFLOW_PATH.is_file():
            self.skipTest(f"{CI_WORKFLOW_PATH} does not exist yet")
        raw = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
        errors = validate_yaml_syntax_and_indentation(raw)
        self.assertEqual(errors, [], f"Syntax errors in {CI_WORKFLOW_PATH}: {errors}")

    def test_release_workflow_no_tab_characters(self):
        if not RELEASE_WORKFLOW_PATH.is_file():
            self.skipTest(f"{RELEASE_WORKFLOW_PATH} does not exist yet")
        raw = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
        errors = validate_yaml_syntax_and_indentation(raw)
        self.assertEqual(errors, [], f"Syntax errors in {RELEASE_WORKFLOW_PATH}: {errors}")


# ---------------------------------------------------------------------------
# Test Suites: CI Workflow Structure
# ---------------------------------------------------------------------------

class TestCIWorkflow(unittest.TestCase):
    """Test suite validating the exact requirements of .github/workflows/ci.yml."""

    @classmethod
    def setUpClass(cls):
        if not CI_WORKFLOW_PATH.is_file():
            raise unittest.SkipTest(f"{CI_WORKFLOW_PATH} does not exist yet")
        cls.raw_yaml = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.data = load_yaml(cls.raw_yaml)

    def test_ci_name_defined(self):
        self.assertEqual(self.data.get("name"), "CI")

    def test_ci_triggers(self):
        on_block = self.data.get("on", {})
        self.assertIn("push", on_block)
        self.assertIn("pull_request", on_block)
        push_branches = on_block["push"].get("branches", [])
        pr_branches = on_block["pull_request"].get("branches", [])
        self.assertIn("main", push_branches)
        self.assertIn("main", pr_branches)

    def test_ci_least_privilege_permissions(self):
        perms = self.data.get("permissions", {})
        self.assertEqual(perms.get("contents"), "read")

    def test_ci_runner_os(self):
        jobs = self.data.get("jobs", {})
        self.assertIn("test", jobs)
        self.assertEqual(jobs["test"].get("runs-on"), "ubuntu-latest")

    def test_ci_matrix_python_versions(self):
        matrix = self.data["jobs"]["test"]["strategy"]["matrix"]
        versions = matrix.get("python-version", [])
        self.assertIn("3.11", versions)
        self.assertIn("3.12", versions)

    def test_ci_offscreen_vtk_env_var(self):
        job_env = self.data["jobs"]["test"].get("env", {})
        vtk_env = job_env.get("VTK_DEFAULT_OPENGL_WINDOW")
        if not vtk_env:
            steps = self.data["jobs"]["test"]["steps"]
            for step in steps:
                if step.get("env", {}).get("VTK_DEFAULT_OPENGL_WINDOW") == "vtkOSOpenGLRenderWindow":
                    vtk_env = "vtkOSOpenGLRenderWindow"
                    break
        self.assertEqual(vtk_env, "vtkOSOpenGLRenderWindow")

    def test_ci_headless_opengl_packages(self):
        steps = self.data["jobs"]["test"]["steps"]
        found_osmesa = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "libosmesa6" in run_cmd:
                found_osmesa = True
                self.assertIn("libgl1", run_cmd)
                self.assertIn("libegl1", run_cmd)
                self.assertIn("libgomp1", run_cmd)
        self.assertTrue(found_osmesa, "Step installing libosmesa6, libgl1, libegl1, libgomp1 not found")

    def test_ci_uv_toolchain_and_caching(self):
        steps = self.data["jobs"]["test"]["steps"]
        found_uv = False
        for step in steps:
            uses = step.get("uses", "")
            if "astral-sh/setup-uv" in uses:
                found_uv = True
                with_block = step.get("with", {})
                self.assertTrue(with_block.get("enable-cache"))
        self.assertTrue(found_uv, "astral-sh/setup-uv action not configured")

    def test_ci_editable_install_with_render(self):
        steps = self.data["jobs"]["test"]["steps"]
        found_install = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "pip install -e" in run_cmd and "render" in run_cmd:
                found_install = True
        self.assertTrue(found_install, "Step installing editable package with 'render' extra not found")

    def test_ci_creates_virtualenv_before_uv_pip_install(self):
        steps = self.data["jobs"]["test"]["steps"]
        venv_created = False
        pip_installed = False
        for step in steps:
            run_cmd = step.get("run", "")
            for line in run_cmd.splitlines():
                stripped = line.strip()
                if stripped.startswith("uv venv"):
                    venv_created = True
                if "uv pip install" in stripped and "--system" not in stripped:
                    self.assertTrue(
                        venv_created,
                        "uv pip install executed before creating a virtual environment with 'uv venv'",
                    )
                    pip_installed = True
        self.assertTrue(pip_installed, "Expected uv pip install step in CI workflow")

        job_env = self.data["jobs"]["test"].get("env", {})
        has_no_sync_env = str(job_env.get("UV_NO_SYNC", "")) in ("1", "true", "True")
        if not has_no_sync_env:
            for step in steps:
                run_cmd = step.get("run", "")
                for line in run_cmd.splitlines():
                    if "uv run" in line:
                        self.assertIn("--no-sync", line, "uv run without UV_NO_SYNC or --no-sync after uv pip install")

    def test_ci_pytest_command(self):
        steps = self.data["jobs"]["test"]["steps"]
        found_test = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "pytest" in run_cmd:
                found_test = True
        self.assertTrue(found_test, "Pytest execution step not found")

    def test_ci_cli_entrypoints_check(self):
        steps = self.data["jobs"]["test"]["steps"]
        found_cli_check = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "bforge --version" in run_cmd or "bforge --help" in run_cmd:
                found_cli_check = True
        self.assertTrue(found_cli_check, "CLI entrypoints verification step not found")


# ---------------------------------------------------------------------------
# Test Suites: Reusable Build and Release Workflow
# ---------------------------------------------------------------------------

class TestReusableBuildAndReleaseWorkflow(unittest.TestCase):
    """Test suite validating the exact requirements of .github/workflows/build-and-release.yml."""

    @classmethod
    def setUpClass(cls):
        if not RELEASE_WORKFLOW_PATH.is_file():
            raise unittest.SkipTest(f"{RELEASE_WORKFLOW_PATH} does not exist yet")
        cls.raw_yaml = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.data = load_yaml(cls.raw_yaml)

    def test_workflow_call_trigger_and_inputs(self):
        on_block = self.data.get("on", {})
        self.assertIn("workflow_call", on_block)
        inputs = on_block["workflow_call"].get("inputs", {})
        self.assertIn("python-version", inputs)
        self.assertEqual(inputs["python-version"].get("default"), "3.12")
        self.assertIn("models-dir", inputs)
        self.assertIn("configs-dir", inputs)
        self.assertIn("dist-dir", inputs)
        self.assertIn("output-assets-dir", inputs)

    def test_contents_write_permissions(self):
        root_perms = self.data.get("permissions", {})
        job_perms = self.data.get("jobs", {}).get("build-and-release", {}).get("permissions", {})
        has_write = root_perms.get("contents") == "write" or job_perms.get("contents") == "write"
        self.assertTrue(has_write, "contents: write permission missing from workflow or job")

    def test_runner_os(self):
        job = self.data["jobs"]["build-and-release"]
        self.assertEqual(job.get("runs-on"), "ubuntu-latest")

    def test_headless_graphics_libraries(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_graphics = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "libosmesa6" in run_cmd:
                found_graphics = True
                self.assertIn("libgl1", run_cmd)
                self.assertIn("libegl1", run_cmd)
                self.assertIn("libgomp1", run_cmd)
        self.assertTrue(found_graphics, "OSMesa and graphics library installation step not found")

    def test_uv_and_locked_sync(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_uv = False
        found_sync = False
        for step in steps:
            if "astral-sh/setup-uv" in str(step.get("uses", "")):
                found_uv = True
            run_cmd = step.get("run", "")
            if "uv sync" in run_cmd:
                found_sync = True
        self.assertTrue(found_uv, "setup-uv action not found")
        self.assertTrue(found_sync, "uv sync step not found")

    def test_check_build_and_source_date_epoch(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_build = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "bforge check" in run_cmd and "bforge build" in run_cmd:
                found_build = True
                self.assertIn("SOURCE_DATE_EPOCH", run_cmd)
        self.assertTrue(found_build, "bforge check and build step with SOURCE_DATE_EPOCH not found")

    def test_upload_artifact_step(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_upload = False
        for step in steps:
            uses = step.get("uses", "")
            if "actions/upload-artifact" in uses:
                found_upload = True
                with_block = step.get("with", {})
                self.assertIn("dist", str(with_block.get("path", "")))
        self.assertTrue(found_upload, "actions/upload-artifact step not found")

    def test_tag_release_guard_and_packaging(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_pkg = False
        for step in steps:
            run_cmd = step.get("run", "")
            if "bforge release-package" in run_cmd:
                found_pkg = True
                if_cond = step.get("if", "")
                self.assertIn("startsWith(github.ref, 'refs/tags/v')", if_cond)
                self.assertIn("--tag", run_cmd)
                self.assertIn("--out", run_cmd)
        self.assertTrue(found_pkg, "bforge release-package step guarded by v* tag not found")

    def test_github_release_action(self):
        steps = self.data["jobs"]["build-and-release"]["steps"]
        found_rel_action = False
        for step in steps:
            uses = step.get("uses", "")
            if "softprops/action-gh-release" in uses:
                found_rel_action = True
                if_cond = step.get("if", "")
                self.assertIn("startsWith(github.ref, 'refs/tags/v')", if_cond)
                with_block = step.get("with", {})
                body_path = str(with_block.get("body_path", ""))
                self.assertIn("RELEASE_NOTES.md", body_path)
                files = str(with_block.get("files", ""))
                self.assertIn("*.zip", files)
                self.assertIn("*.stl", files)
                self.assertIn("*.step", files)
                self.assertIn("*.png", files)
                self.assertIn("SHA256SUMS.txt", files)
        self.assertTrue(found_rel_action, "softprops/action-gh-release step not found or incomplete")


# ---------------------------------------------------------------------------
# Test Suites: Consumer Caller Contract
# ---------------------------------------------------------------------------

class TestConsumerCallerWorkflow(unittest.TestCase):
    """Test suite validating the consumer repository caller contract."""

    def test_consumer_caller_yaml_structure(self):
        data = load_yaml(CONSUMER_RELEASE_YAML_SAMPLE)
        self.assertIn("push", data.get("on", {}))
        self.assertIn("pull_request", data.get("on", {}))
        job = data.get("jobs", {}).get("models", {})
        self.assertIn("build123d-forge/build123d-forge/.github/workflows/build-and-release.yml", job.get("uses", ""))
        self.assertEqual(job.get("permissions", {}).get("contents"), "write")


# ---------------------------------------------------------------------------
# Test Suites: Embedded YAML Parser Unit Tests
# ---------------------------------------------------------------------------

class TestYamlParserUnitTests(unittest.TestCase):
    """Test suite for the embedded zero-dependency YAML parser."""

    def test_parser_handles_nested_maps(self):
        text = "a:\n  b:\n    c: 123\n"
        data = load_yaml(text)
        self.assertEqual(data, {"a": {"b": {"c": 123}}})

    def test_parser_handles_lists_of_dicts(self):
        text = "items:\n  - name: one\n    val: 1\n  - name: two\n    val: 2\n"
        data = load_yaml(text)
        self.assertEqual(data, {"items": [{"name": "one", "val": 1}, {"name": "two", "val": 2}]})

    def test_parser_handles_multiline_strings(self):
        text = "script: |\n  echo first\n  echo second\n"
        data = load_yaml(text)
        self.assertIn("echo first", data["script"])
        self.assertIn("echo second", data["script"])

    def test_parser_handles_flow_sequences(self):
        text = 'branches: [main, develop]\nversions: ["3.11", "3.12"]\n'
        data = load_yaml(text)
        self.assertEqual(data["branches"], ["main", "develop"])
        self.assertEqual(data["versions"], ["3.11", "3.12"])

    def test_parser_handles_booleans_and_numbers(self):
        text = "flag_t: true\nflag_f: false\nnum_int: 42\nnum_float: 3.14\n"
        data = load_yaml(text)
        self.assertIs(data["flag_t"], True)
        self.assertIs(data["flag_f"], False)
        self.assertEqual(data["num_int"], 42)
        self.assertEqual(data["num_float"], 3.14)

    def test_parser_handles_comments_and_blank_lines(self):
        text = "# Comment\n\nkey: value # inline comment\n\n# Another comment\n"
        data = load_yaml(text)
        self.assertIn("key", data)
        self.assertEqual(data["key"], "value")

    def test_parser_handles_inline_comments_on_numbers_and_booleans(self):
        text = "count: 42 # inline comment\nenabled: true # comment\nratio: 3.14 # pi\n"
        data = load_yaml(text)
        self.assertEqual(data["count"], 42)
        self.assertIs(data["enabled"], True)
        self.assertAlmostEqual(data["ratio"], 3.14)

    def test_parser_preserves_quoted_hash_characters(self):
        text = 'msg1: "hello # world" # comment\nmsg2: \'single # quoted\' # comment2\n'
        data = load_yaml(text)
        self.assertEqual(data["msg1"], "hello # world")
        self.assertEqual(data["msg2"], "single # quoted")

    def test_parser_preserves_block_scalar_indentation(self):
        text = (
            "script: |\n"
            "  def foo():\n"
            "      return 42\n"
            "\n"
            "      x = 10\n"
        )
        data = load_yaml(text)
        lines = data["script"].splitlines()
        self.assertEqual(lines[0], "def foo():")
        self.assertEqual(lines[1], "    return 42")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "    x = 10")

    def test_parser_handles_quoted_colon_in_sequence(self):
        text = 'tags:\n  - "tag:v1.0"\n  - "tag:v2.0"\n'
        data = load_yaml(text)
        self.assertEqual(data["tags"], ["tag:v1.0", "tag:v2.0"])


# ---------------------------------------------------------------------------
# Test Suites: Command & Option Conformance with Click Specification
# ---------------------------------------------------------------------------

class TestWorkflowCLICommandConformance(unittest.TestCase):
    """Test suite ensuring all CLI commands and options in workflows match Click definitions."""

    @classmethod
    def setUpClass(cls):
        cls.cli = cli

    def _get_allowed_options(self, subcmd_name: str | None) -> set[str]:
        """Return allowed options for root cli or a subcommand."""
        allowed = {"--help", "-h"}
        if subcmd_name is None:
            for p in self.cli.params:
                if isinstance(p, click.Option):
                    allowed.update(p.opts)
                    allowed.update(p.secondary_opts)
            allowed.update({"--version", "-v"})
            return allowed

        self.assertIn(subcmd_name, self.cli.commands, f"Unknown bforge subcommand '{subcmd_name}'")
        cmd = self.cli.commands[subcmd_name]
        for p in cmd.params:
            if isinstance(p, click.Option):
                allowed.update(p.opts)
                allowed.update(p.secondary_opts)
        return allowed

    @staticmethod
    def _extract_commands(text: str) -> list[str]:
        """Extract individual logical shell commands handling backslash continuations."""
        commands = []
        current = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if current:
                current += " " + line
            else:
                current = line
            if current.endswith("\\"):
                current = current[:-1].strip()
            else:
                commands.append(current)
                current = ""
        if current:
            commands.append(current)
        return commands

    def _validate_command_line(self, cmd_line: str) -> list[str]:
        """Validate a shell command string containing bforge or build123d-forge."""
        errors = []
        try:
            tokens = shlex.split(cmd_line)
        except ValueError:
            tokens = cmd_line.split()

        for idx, t in enumerate(tokens):
            if t in ("bforge", "build123d-forge"):
                subargs = tokens[idx + 1:]
                if not subargs or subargs[0].startswith("-"):
                    allowed = self._get_allowed_options(None)
                    for a in subargs:
                        if a.startswith("-"):
                            opt = a.split("=", 1)[0]
                            if opt not in allowed:
                                errors.append(f"Root CLI does not accept option '{opt}' in '{cmd_line}'")
                else:
                    subcmd = subargs[0]
                    if subcmd not in self.cli.commands:
                        errors.append(f"Unknown subcommand '{subcmd}' in '{cmd_line}'")
                        continue
                    allowed = self._get_allowed_options(subcmd)
                    for a in subargs[1:]:
                        if a.startswith("-"):
                            opt = a.split("=", 1)[0]
                            if opt not in allowed:
                                errors.append(f"Subcommand '{subcmd}' does not accept option '{opt}' in '{cmd_line}'")
        return errors

    def test_reusable_workflow_cli_conformance(self):
        """Verify that all bforge invocations in build-and-release.yml use valid Click commands and options."""
        raw = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
        data = load_yaml(raw)
        jobs = data.get("jobs", {})
        all_errors = []
        bforge_found = 0
        for j_name, j_def in jobs.items():
            if not isinstance(j_def, dict):
                continue
            for s in j_def.get("steps", []):
                run_text = s.get("run", "")
                for cmd in self._extract_commands(run_text):
                    if "bforge" in cmd or "build123d-forge" in cmd:
                        bforge_found += 1
                        errs = self._validate_command_line(cmd)
                        for e in errs:
                            all_errors.append(f"[{j_name} | {s.get('name')}] {e}")
        self.assertGreater(bforge_found, 0, "No bforge commands found in build-and-release.yml")
        self.assertEqual(all_errors, [], f"CLI conformance errors in build-and-release.yml: {all_errors}")

    def test_ci_workflow_cli_conformance(self):
        """Verify that all bforge invocations in ci.yml use valid Click commands and options."""
        raw = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
        data = load_yaml(raw)
        jobs = data.get("jobs", {})
        all_errors = []
        bforge_found = 0
        for j_name, j_def in jobs.items():
            if not isinstance(j_def, dict):
                continue
            for s in j_def.get("steps", []):
                run_text = s.get("run", "")
                for cmd in self._extract_commands(run_text):
                    if "bforge" in cmd or "build123d-forge" in cmd:
                        bforge_found += 1
                        errs = self._validate_command_line(cmd)
                        for e in errs:
                            all_errors.append(f"[{j_name} | {s.get('name')}] {e}")
        self.assertGreater(bforge_found, 0, "No bforge commands found in ci.yml")
        self.assertEqual(all_errors, [], f"CLI conformance errors in ci.yml: {all_errors}")

    def test_cli_conformance_validator_detects_invalid_options(self):
        """Verify that the conformance validator correctly flags illegal Click options."""
        err1 = self._validate_command_line("uv run bforge build --invalid-option .ci_smoke/models -o dist")
        self.assertTrue(any("does not accept option '--invalid-option'" in e for e in err1), f"Expected error: {err1}")

        err2 = self._validate_command_line("bforge check --unknown-flag")
        self.assertTrue(any("does not accept option '--unknown-flag'" in e for e in err2), f"Expected error: {err2}")

        err3 = self._validate_command_line("bforge nonexistent-command")
        self.assertTrue(any("Unknown subcommand 'nonexistent-command'" in e for e in err3), f"Expected error: {err3}")

        # Valid commands should return no errors
        self.assertEqual(self._validate_command_line("uv run bforge check"), [])
        self.assertEqual(self._validate_command_line("uv run bforge build -o dist"), [])
        self.assertEqual(self._validate_command_line("uv run bforge build --models-dir .ci_smoke/models -o dist"), [])
        self.assertEqual(self._validate_command_line("uv run bforge release-package --tag v1.0.0 --dist dist --out assets"), [])


if __name__ == "__main__":
    unittest.main()
