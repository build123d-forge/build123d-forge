"""Unit tests for build123d_forge.cache subsystem.

Verifies:
- Python AST import closure resolution and circular dependency protection.
- Comment, blank line, and CRLF invariance of AST closure hashes.
- Canonical parameter serialization and float normalization.
- BuildManifest generation, schema fields, and serialization.
- Sub-50ms cache hits and invalidation on parameter, code, or artifact modifications.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from build123d_forge.cache import (
    BuildManifest,
    canonical_param_json,
    canonicalize_params,
    clear_cache,
    compute_ast_closure_hash,
    compute_manifest,
    compute_param_hash,
    find_project_root,
    get_dependency_versions,
    is_cache_valid,
)


class DummyModel(BaseModel):
    """Simple model for parameter testing."""

    length: float = 20.0
    width: float = 10.0
    material: str = "PLA"
    tags: list[str] = ["prototype", "v1"]


class TestAstClosureHashing(unittest.TestCase):
    """Test suite for compute_ast_closure_hash and local dependency walking."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_single_file_hash_stability(self) -> None:
        """compute_ast_closure_hash produces reproducible 64-char sha256 hex string."""
        src = self.dir / "model.py"
        src.write_text("class Model:\n    val = 42\n", encoding="utf-8")

        h1 = compute_ast_closure_hash(src, project_root=self.dir)
        h2 = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h1))

    def test_comment_and_blank_line_invariance(self) -> None:
        """Adding comments, docstrings, or blank lines does not alter AST hash."""
        src = self.dir / "model.py"
        src.write_text("class Model:\n    val = 42\n", encoding="utf-8")
        h1 = compute_ast_closure_hash(src, project_root=self.dir)

        # Modify with comments and blank lines
        src.write_text(
            "# Module header comment\n\n"
            "class Model:\n"
            "    # Field comment\n"
            "    val = 42\n\n\n",
            encoding="utf-8",
        )
        h2 = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertEqual(h1, h2)

    def test_crlf_vs_lf_invariance(self) -> None:
        """Windows CRLF and Unix LF line endings produce identical AST closure hashes."""
        src1 = self.dir / "m_crlf.py"
        src2 = self.dir / "m_lf.py"
        src1.write_bytes(b"class Model:\r\n    val = 42\r\n")
        src2.write_bytes(b"class Model:\n    val = 42\n")

        # Use same filename identifier context by writing to same file
        target = self.dir / "model.py"
        target.write_bytes(b"class Model:\r\n    val = 42\r\n")
        h_crlf = compute_ast_closure_hash(target, project_root=self.dir)

        target.write_bytes(b"class Model:\n    val = 42\n")
        h_lf = compute_ast_closure_hash(target, project_root=self.dir)

        self.assertEqual(h_crlf, h_lf)

    def test_semantic_change_alters_hash(self) -> None:
        """Changing logic or values in source alters the AST closure hash."""
        src = self.dir / "model.py"
        src.write_text("class Model:\n    val = 42\n", encoding="utf-8")
        h1 = compute_ast_closure_hash(src, project_root=self.dir)

        src.write_text("class Model:\n    val = 99\n", encoding="utf-8")
        h2 = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertNotEqual(h1, h2)

    def test_relative_import_traversal(self) -> None:
        """Relative imports within the project tree are discovered and traversed."""
        pkg = self.dir / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        helper = pkg / "helper.py"
        helper.write_text("CONST = 100\n", encoding="utf-8")

        main = pkg / "main.py"
        main.write_text("from .helper import CONST\nclass Part:\n    x = CONST\n", encoding="utf-8")

        h1 = compute_ast_closure_hash(main, project_root=self.dir)

        # Modifying helper alters main's closure hash
        helper.write_text("CONST = 200\n", encoding="utf-8")
        h2 = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertNotEqual(h1, h2)

    def test_circular_dependency_protection(self) -> None:
        """Circular imports (a imports b, b imports a) terminate cleanly without recursion error."""
        mod_a = self.dir / "mod_a.py"
        mod_b = self.dir / "mod_b.py"

        mod_a.write_text("import mod_b\nA = 1\n", encoding="utf-8")
        mod_b.write_text("import mod_a\nB = 2\n", encoding="utf-8")

        h_a = compute_ast_closure_hash(mod_a, project_root=self.dir)
        self.assertEqual(len(h_a), 64)

        # Stability check
        h_a2 = compute_ast_closure_hash(mod_a, project_root=self.dir)
        self.assertEqual(h_a, h_a2)

    def test_standard_library_and_site_packages_ignored(self) -> None:
        """Standard library and third-party package imports are not treated as local files."""
        src = self.dir / "model.py"
        src.write_text(
            "import os\n"
            "import sys\n"
            "import json\n"
            "import math\n"
            "from typing import Any, Optional\n"
            "import pydantic\n"
            "class M:\n"
            "    pass\n",
            encoding="utf-8",
        )
        h = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertEqual(len(h), 64)

    def test_missing_source_file_raises_error(self) -> None:
        """compute_ast_closure_hash raises FileNotFoundError if source does not exist."""
        with self.assertRaises(FileNotFoundError):
            compute_ast_closure_hash(self.dir / "nonexistent.py", project_root=self.dir)

    def test_syntax_error_fallback(self) -> None:
        """Unparseable Python syntax falls back to raw code normalization without crashing."""
        src = self.dir / "broken.py"
        src.write_text("def broken(:\n", encoding="utf-8")
        h = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertEqual(len(h), 64)

    def test_deleted_dependency_resilience(self) -> None:
        """Deleting an imported helper file cleanly invalidates and recomputes closure without crashing."""
        helper = self.dir / "resilient_dep.py"
        helper.write_text("VALUE = 123\n", encoding="utf-8")
        main = self.dir / "main_resilient.py"
        main.write_text("import resilient_dep\nclass M:\n    v = resilient_dep.VALUE\n", encoding="utf-8")

        h1 = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertEqual(len(h1), 64)

        # Remove the imported dependency
        helper.unlink()

        # Closure computation must not raise FileNotFoundError, and must return updated hash
        h2 = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertEqual(len(h2), 64)
        self.assertNotEqual(h1, h2)

        # Repeated evaluation is stable and cached
        h3 = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertEqual(h2, h3)

    def test_late_module_creation_discovery(self) -> None:
        """Creating an imported module after an initial miss is discovered without requiring clear_cache()."""
        main = self.dir / "main_late.py"
        main.write_text("import late_module\nclass M:\n    pass\n", encoding="utf-8")

        # Initial evaluation before late_module.py exists
        h_before = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertEqual(len(h_before), 64)

        # Now create late_module.py on disk
        late_mod = self.dir / "late_module.py"
        late_mod.write_text("DATA = 'hello'\n", encoding="utf-8")

        # Modify main to trigger closure re-evaluation
        main.write_text("import late_module\nclass M:\n    val = late_module.DATA\n", encoding="utf-8")
        h_after = compute_ast_closure_hash(main, project_root=self.dir)

        # The newly created module must be discovered and reflected in the closure hash
        self.assertNotEqual(h_before, h_after)

        # Verify that explicit cache clearing produces the identical hash
        clear_cache()
        h_clean = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertEqual(h_after, h_clean)


class TestParamCanonicalization(unittest.TestCase):
    """Test suite for canonical parameter normalization and hashing."""

    def test_key_ordering_invariance(self) -> None:
        """Dictionaries with different key insertion orders produce identical canonical JSON and hash."""
        p1 = {"b": 2.0, "a": 1.0, "c": 3.0}
        p2 = {"c": 3.0, "b": 2.0, "a": 1.0}

        json1 = canonical_param_json(p1)
        json2 = canonical_param_json(p2)
        self.assertEqual(json1, json2)
        self.assertEqual(json1, '{"a":1.0,"b":2.0,"c":3.0}')

        h1 = compute_param_hash(p1)
        h2 = compute_param_hash(p2)
        self.assertEqual(h1, h2)

    def test_float_zero_normalization(self) -> None:
        """Negative zero (-0.0) normalizes to positive zero (0.0)."""
        p1 = {"offset": -0.0}
        p2 = {"offset": 0.0}

        self.assertEqual(canonical_param_json(p1), canonical_param_json(p2))
        self.assertEqual(compute_param_hash(p1), compute_param_hash(p2))

    def test_float_precision_jitter(self) -> None:
        """Floats with negligible representation jitter round consistently."""
        p1 = {"val": 1.0000000000001}
        p2 = {"val": 1.00000000000010001}

        self.assertEqual(compute_param_hash(p1), compute_param_hash(p2))

    def test_nested_collection_canonicalization(self) -> None:
        """Nested dictionaries and lists are normalized recursively."""
        nested = {
            "geom": {"height": 5.0, "width": 10.0},
            "layers": [
                {"name": "base", "thickness": 2.0},
                {"name": "cap", "thickness": 1.5},
            ],
        }
        res_json = canonical_param_json(nested)
        self.assertIn('"height":5.0', res_json)
        self.assertIn('"thickness":2.0', res_json)
        # Verify compact separators without spaces
        self.assertNotIn(": ", res_json)
        self.assertNotIn(", ", res_json)

    def test_pydantic_model_parameter_hash(self) -> None:
        """compute_param_hash accepts Pydantic BaseModel instances directly."""
        m = DummyModel(length=30.0, width=15.0)
        h = compute_param_hash(m)
        self.assertEqual(len(h), 64)

        # Identical instance produces identical hash
        m_same = DummyModel(length=30.0, width=15.0)
        self.assertEqual(h, compute_param_hash(m_same))

        # Different instance produces different hash
        m_diff = DummyModel(length=35.0, width=15.0)
        self.assertNotEqual(h, compute_param_hash(m_diff))


class TestManifestModelAndSchema(unittest.TestCase):
    """Test suite for BuildManifest and compute_manifest."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_manifest_schema_fields(self) -> None:
        """BuildManifest satisfies schema requirements and defaults."""
        m = BuildManifest(
            schema_version=1,
            model_name="BoxModel",
            variant_name="default",
            ast_hash="aaa",
            param_hash="bbb",
            input_sha256="ccc",
            resolved_params={"length": 20.0},
            dependencies={"python": "3.12"},
            artifacts={"box.step": "hash123"},
        )
        self.assertEqual(m.schema_version, 1)
        self.assertEqual(m.model_name, "BoxModel")
        self.assertEqual(m.variant_name, "default")
        self.assertEqual(m.resolved_params["length"], 20.0)
        self.assertEqual(m.artifacts["box.step"], "hash123")

    def test_compute_manifest_with_artifacts(self) -> None:
        """compute_manifest populates input closure, parameter hashes, and artifact digests."""
        src = self.dir / "model.py"
        src.write_text("class BoxModel:\n    pass\n", encoding="utf-8")

        art = self.dir / "model.step"
        art.write_bytes(b"STEP FILE DATA")

        model_inst = DummyModel(length=25.0)
        manifest = compute_manifest(
            model_instance=model_inst,
            source_file=src,
            variant_name="preset_a",
            project_root=self.dir,
            artifacts={"model.step": art},
        )

        self.assertEqual(manifest.schema_version, 1)
        self.assertEqual(manifest.variant_name, "preset_a")
        self.assertEqual(manifest.resolved_params["length"], 25.0)
        self.assertIn("model.step", manifest.artifacts)
        expected_art_hash = hashlib.sha256(b"STEP FILE DATA").hexdigest()
        self.assertEqual(manifest.artifacts["model.step"], expected_art_hash)

        # Dependencies present
        self.assertIn("python", manifest.dependencies)
        self.assertIn("pydantic", manifest.dependencies)

    def test_manifest_save_and_load(self) -> None:
        """Manifest can be saved to disk and reloaded without loss of fidelity."""
        src = self.dir / "model.py"
        src.write_text("class M: pass\n", encoding="utf-8")
        manifest = compute_manifest(DummyModel(), src, variant_name="v1", project_root=self.dir)

        dest = self.dir / "dist" / "v1" / "manifest.json"
        saved = manifest.save(dest)
        self.assertTrue(saved.is_file())

        loaded = BuildManifest.load(dest)
        self.assertEqual(manifest.input_sha256, loaded.input_sha256)
        self.assertEqual(manifest.resolved_params, loaded.resolved_params)

    def test_get_dependency_versions_memoization(self) -> None:
        """get_dependency_versions is memoized with lru_cache and supports cache_clear."""
        get_dependency_versions.cache_clear()

        v1 = get_dependency_versions()
        self.assertIn("python", v1)
        self.assertIn("pydantic", v1)

        v2 = get_dependency_versions()
        self.assertEqual(v1, v2)
        if hasattr(get_dependency_versions, "cache_info"):
            self.assertGreaterEqual(get_dependency_versions.cache_info().hits, 1)

        get_dependency_versions.cache_clear()
        if hasattr(get_dependency_versions, "cache_info"):
            self.assertEqual(get_dependency_versions.cache_info().hits, 0)



class TestCacheValidationAndPerformance(unittest.TestCase):
    """Test suite for is_cache_valid and sub-50ms execution verification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_cache_hit(self) -> None:
        """is_cache_valid returns True when manifest matches current inputs and artifacts."""
        src = self.dir / "proto.py"
        src.write_text("class Proto: pass\n", encoding="utf-8")
        art = self.dir / "part.step"
        art.write_bytes(b"ORIGINAL DATA")

        model = DummyModel()
        manifest = compute_manifest(model, src, "default", project_root=self.dir, artifacts={"part.step": art})
        man_path = self.dir / "manifest.json"
        manifest.save(man_path)

        self.assertTrue(is_cache_valid(man_path, manifest))

    def test_invalid_when_manifest_missing(self) -> None:
        """is_cache_valid returns False when manifest.json does not exist."""
        manifest = BuildManifest()
        self.assertFalse(is_cache_valid(self.dir / "missing_manifest.json", manifest))

    def test_invalid_when_manifest_corrupted(self) -> None:
        """is_cache_valid returns False when manifest.json is invalid JSON."""
        man_path = self.dir / "manifest.json"
        man_path.write_text("{corrupt: [json", encoding="utf-8")
        manifest = BuildManifest()
        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_invalid_when_parameter_modified(self) -> None:
        """is_cache_valid returns False when model parameters differ from cached manifest."""
        src = self.dir / "proto.py"
        src.write_text("class Proto: pass\n", encoding="utf-8")

        m1 = compute_manifest(DummyModel(length=20.0), src, "default", project_root=self.dir)
        m2 = compute_manifest(DummyModel(length=25.0), src, "default", project_root=self.dir)

        man_path = self.dir / "manifest.json"
        m1.save(man_path)

        self.assertTrue(is_cache_valid(man_path, m1))
        self.assertFalse(is_cache_valid(man_path, m2))

    def test_invalid_when_source_code_modified(self) -> None:
        """is_cache_valid returns False when source code logic changes."""
        src = self.dir / "proto.py"
        src.write_text("VAL = 1\n", encoding="utf-8")

        m1 = compute_manifest(DummyModel(), src, "default", project_root=self.dir)
        man_path = self.dir / "manifest.json"
        m1.save(man_path)

        src.write_text("VAL = 2\n", encoding="utf-8")
        m2 = compute_manifest(DummyModel(), src, "default", project_root=self.dir)
        self.assertFalse(is_cache_valid(man_path, m2))

    def test_invalid_when_artifact_deleted(self) -> None:
        """is_cache_valid returns False when an output artifact is removed."""
        src = self.dir / "proto.py"
        src.write_text("class P: pass\n", encoding="utf-8")
        art = self.dir / "out.stl"
        art.write_bytes(b"STL MESH")

        manifest = compute_manifest(DummyModel(), src, "default", project_root=self.dir, artifacts={"out.stl": art})
        man_path = self.dir / "manifest.json"
        manifest.save(man_path)

        self.assertTrue(is_cache_valid(man_path, manifest))
        art.unlink()
        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_invalid_when_artifact_tampered(self) -> None:
        """is_cache_valid returns False when an artifact's content is modified."""
        src = self.dir / "proto.py"
        src.write_text("class P: pass\n", encoding="utf-8")
        art = self.dir / "out.stl"
        art.write_bytes(b"ORIGINAL MESH")

        manifest = compute_manifest(DummyModel(), src, "default", project_root=self.dir, artifacts={"out.stl": art})
        man_path = self.dir / "manifest.json"
        manifest.save(man_path)

        self.assertTrue(is_cache_valid(man_path, manifest))
        art.write_bytes(b"TAMPERED MESH CORRUPTED")
        self.assertFalse(is_cache_valid(man_path, manifest))

    def test_sub_50ms_execution_performance(self) -> None:
        """Cache validation executes well under the 50ms budget over multiple runs."""
        src = self.dir / "proto.py"
        src.write_text("class Fast: pass\n", encoding="utf-8")

        # Create multiple simulated artifacts (STEP, STL, PNG)
        artifacts: dict[str, Path] = {}
        for name in ("model.step", "model.stl", "model_print.stl", "preview.png"):
            p = self.dir / name
            p.write_bytes(b"PREVIEW AND CAD DATA" * 500)
            artifacts[name] = p

        manifest = compute_manifest(DummyModel(), src, "default", project_root=self.dir, artifacts=artifacts)
        man_path = self.dir / "manifest.json"
        manifest.save(man_path)

        # Benchmark 50 iterations
        durations_ms: list[float] = []
        for _ in range(50):
            start = time.perf_counter()
            valid = is_cache_valid(man_path, manifest)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.assertTrue(valid)
            durations_ms.append(elapsed_ms)

        avg_ms = sum(durations_ms) / len(durations_ms)
        max_ms = max(durations_ms)

        # Budget is 50.0 ms; typical execution should be < 5.0 ms
        self.assertLess(avg_ms, 10.0, f"Average cache check too slow: {avg_ms:.2f} ms")
        self.assertLess(max_ms, 50.0, f"Max cache check exceeded 50ms limit: {max_ms:.2f} ms")

    def test_cache_clear(self) -> None:
        """clear_cache() flushes all memoized AST closures, import resolutions, and artifact hashes."""
        src = self.dir / "model.py"
        src.write_text("class M: pass\n", encoding="utf-8")
        art = self.dir / "out.stl"
        art.write_bytes(b"STL DATA")

        # Populate AST closure cache and artifact hash cache
        h1 = compute_ast_closure_hash(src, project_root=self.dir)
        _ = get_dependency_versions()
        manifest = compute_manifest(DummyModel(), src, "default", project_root=self.dir, artifacts={"out.stl": art})
        man_path = self.dir / "manifest.json"
        manifest.save(man_path)
        self.assertTrue(is_cache_valid(man_path, manifest))

        # Check internal caches are populated
        from build123d_forge.cache import _ARTIFACT_HASH_CACHE, _AST_CLOSURE_CACHE

        self.assertGreater(len(_AST_CLOSURE_CACHE), 0)
        self.assertGreater(len(_ARTIFACT_HASH_CACHE), 0)

        # Clear cache
        clear_cache()

        # Check internal caches are cleared and lru_cache was reset
        self.assertEqual(len(_AST_CLOSURE_CACHE), 0)
        self.assertEqual(len(_ARTIFACT_HASH_CACHE), 0)
        if hasattr(get_dependency_versions, "cache_info"):
            self.assertEqual(get_dependency_versions.cache_info().hits, 0)

        # Confirm operations work seamlessly after clearing
        h2 = compute_ast_closure_hash(src, project_root=self.dir)
        self.assertEqual(h1, h2)
        self.assertTrue(is_cache_valid(man_path, manifest))


class TestAstInvalidationAndConcurrency(unittest.TestCase):
    """Edge cases for dynamic dependency creation, package fallback overrides, and multi-threaded caching."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp_dir.name).resolve()
        clear_cache()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_late_subpackage_module_overrides_init_fallback(self) -> None:
        """Creating pkg/submod.py invalidates a previously cached fallback to pkg/__init__.py."""
        pkg = self.dir / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("DEFAULT_VAL = 1\n", encoding="utf-8")
        main = self.dir / "main.py"
        main.write_text("from pkg import submod\n", encoding="utf-8")

        # Initially resolves fallback to pkg/__init__.py because pkg/submod.py does not exist
        h_before = compute_ast_closure_hash(main, project_root=self.dir)

        # Now create pkg/submod.py without touching main.py or calling clear_cache()
        (pkg / "submod.py").write_text("SUBMOD_VAL = 999\n", encoding="utf-8")
        h_after = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertNotEqual(h_before, h_after)

        clear_cache()
        self.assertEqual(h_after, compute_ast_closure_hash(main, project_root=self.dir))

    def test_rapid_dependency_deletion_and_recreation_cycle(self) -> None:
        """Deleting and recreating a transitive dependency without editing root invalidates AST closure."""
        dep = self.dir / "helper.py"
        main = self.dir / "root.py"
        main.write_text("import helper\n", encoding="utf-8")

        dep.write_text("X = 10\n", encoding="utf-8")
        h_v1 = compute_ast_closure_hash(main, project_root=self.dir)

        dep.unlink()
        h_missing = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertNotEqual(h_v1, h_missing)

        dep.write_text("X = 20\n", encoding="utf-8")
        h_v2 = compute_ast_closure_hash(main, project_root=self.dir)
        self.assertNotEqual(h_missing, h_v2)
        self.assertNotEqual(h_v1, h_v2)

    def test_multithreaded_closure_hash_consistency(self) -> None:
        """Concurrent threads computing AST closure hashes observe deterministic, race-free results."""
        from concurrent.futures import ThreadPoolExecutor

        (self.dir / "shared.py").write_text("FACTOR = 3.5\n", encoding="utf-8")
        main = self.dir / "entry.py"
        main.write_text("import shared\nclass Entry:\n    k = shared.FACTOR\n", encoding="utf-8")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: compute_ast_closure_hash(main, project_root=self.dir), range(32)))

        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(results[0]), 64)


if __name__ == "__main__":
    unittest.main()
