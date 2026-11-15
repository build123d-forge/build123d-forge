"""Comprehensive unit test suite for build123d_forge.config.

Tests configuration loading, inheritance resolution, cycle detection,
fuzzy typo suggestions via difflib, directives stripping, and scaffolding.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import tomllib
from typing import Any, Literal, Self, Union
import unittest

from pydantic import BaseModel, Field, ValidationError, model_validator

from build123d_forge.config import (
    ConfigError,
    create_config_template,
    discover_config_files,
    get_typo_suggestion,
    load_config,
    load_config_file,
    read_config_file,
    scaffold_config,
)
from build123d_forge.prototype import ModelPrototype, Param


class SubSpec(BaseModel):
    model_config = {"extra": "forbid"}
    sub_width: float = 5.0
    sub_height: float = 10.0



class AltSubSpec(BaseModel):
    model_config = {"extra": "forbid"}
    alt_diameter: float = 4.0
    alt_length: float = 12.0

class ConfigTestBox(ModelPrototype):
    length: float = Param(20.0, gt=0.0, lt=100.0, unit="mm", description="Box length")
    width: float = Param(10.0, gt=0.0, unit="mm", description="Box width")
    wall_gap: float = Param(9.625, gt=0.0, unit="mm", description="Glass slot gap")
    wall_thickness: float = Param(3.0, gt=0.0, unit="mm", description="Wall thickness")
    visor_drop: float = Param(10.0, gt=0.0, unit="mm", description="Visor drop")
    handedness: Literal["left", "right"] = Param("left", description="Handedness")
    spec: SubSpec = SubSpec()

    @model_validator(mode="after")
    def _validate_geometry(self) -> Self:
        if self.visor_drop < 2.0 * self.wall_thickness:
            raise ValueError("visor_drop must be at least 2x wall_thickness")
        return self

    def build(self):
        from build123d import Box
        return Box(self.length, self.width, self.visor_drop)


class TestConfigModuleExports(unittest.TestCase):
    """Test public API exports and exception behaviors."""

    def test_exports_callable(self):
        self.assertTrue(callable(load_config_file))
        self.assertTrue(callable(load_config))
        self.assertTrue(callable(read_config_file))
        self.assertTrue(callable(discover_config_files))
        self.assertTrue(callable(scaffold_config))
        self.assertTrue(callable(create_config_template))
        self.assertTrue(issubclass(ConfigError, Exception))

    def test_config_error_string_formatting(self):
        err_with_path = ConfigError("Syntax failure", path="/path/to/cfg.toml")
        self.assertEqual(str(err_with_path), "/path/to/cfg.toml: Syntax failure")
        self.assertEqual(err_with_path.path, Path("/path/to/cfg.toml"))

        err_no_path = ConfigError("Generic error")
        self.assertEqual(str(err_no_path), "Generic error")
        self.assertIsNone(err_no_path.path)

        # When message already starts with path, do not duplicate
        err_prefixed = ConfigError("/path/to/cfg.toml: Already prefixed", path="/path/to/cfg.toml")
        self.assertEqual(str(err_prefixed), "/path/to/cfg.toml: Already prefixed")


class TestConfigBasicLoading(unittest.TestCase):
    """Test standard single-file loading workflows and basic validation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_full_config(self):
        f = self.root / "full.toml"
        f.write_text(
            "length = 50.0\n"
            "width = 25.0\n"
            "wall_gap = 12.0\n"
            "wall_thickness = 4.0\n"
            "visor_drop = 15.0\n"
            "handedness = 'right'\n",
            encoding="utf-8",
        )
        inst = load_config_file(f, ConfigTestBox)
        self.assertEqual(inst.length, 50.0)
        self.assertEqual(inst.width, 25.0)
        self.assertEqual(inst.wall_gap, 12.0)
        self.assertEqual(inst.wall_thickness, 4.0)
        self.assertEqual(inst.visor_drop, 15.0)
        self.assertEqual(inst.handedness, "right")

    def test_load_sparse_config(self):
        f = self.root / "sparse.toml"
        f.write_text("length = 35.0\n", encoding="utf-8")
        inst = load_config_file(f, ConfigTestBox)
        self.assertEqual(inst.length, 35.0)
        self.assertEqual(inst.width, 10.0)  # default
        self.assertEqual(inst.wall_gap, 9.625)  # default

    def test_load_empty_config(self):
        f = self.root / "empty.toml"
        f.write_text("", encoding="utf-8")
        inst = load_config_file(f, ConfigTestBox)
        self.assertEqual(inst.length, 20.0)
        self.assertEqual(inst.wall_gap, 9.625)

    def test_load_nonexistent_file_raises_config_error(self):
        missing = self.root / "does_not_exist.toml"
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(missing, ConfigTestBox)
        msg = str(ctx.exception).lower()
        self.assertTrue("not found" in msg or "does not exist" in msg)

    def test_load_empty_path_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_config_file("", ConfigTestBox)

    def test_load_syntax_error_raises_config_error(self):
        f = self.root / "syntax_error.toml"
        f.write_text("length = [unterminated_array\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        self.assertIn("parse", str(ctx.exception).lower())

    def test_accepts_str_path_and_alias(self):
        f = self.root / "string_path.toml"
        f.write_text("length = 42.0\n", encoding="utf-8")
        inst = load_config(str(f), ConfigTestBox)
        self.assertEqual(inst.length, 42.0)


class TestConfigExtendsResolution(unittest.TestCase):
    """Test recursive extends inheritance, path resolution, and directive stripping."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_level_extends(self):
        (self.root / "base.toml").write_text("length = 30.0\nwidth = 20.0\n", encoding="utf-8")
        (self.root / "child.toml").write_text('extends = "base.toml"\nwidth = 25.0\n', encoding="utf-8")
        inst = load_config_file(self.root / "child.toml", ConfigTestBox)
        self.assertEqual(inst.length, 30.0)
        self.assertEqual(inst.width, 25.0)

    def test_multi_level_extends_chain(self):
        (self.root / "a.toml").write_text("length = 10.0\nwidth = 10.0\nwall_gap = 5.0\n", encoding="utf-8")
        (self.root / "b.toml").write_text('extends = "a.toml"\nwidth = 20.0\n', encoding="utf-8")
        (self.root / "c.toml").write_text('extends = "b.toml"\nwall_gap = 15.0\n', encoding="utf-8")
        inst = load_config_file(self.root / "c.toml", ConfigTestBox)
        self.assertEqual(inst.length, 10.0)
        self.assertEqual(inst.width, 20.0)
        self.assertEqual(inst.wall_gap, 15.0)

    def test_extends_fallback_to_configs_dir(self):
        cfg_dir = self.root / "shared_configs"
        cfg_dir.mkdir()
        (cfg_dir / "common.toml").write_text("length = 77.0\n", encoding="utf-8")

        sub_dir = self.root / "local"
        sub_dir.mkdir()
        child = sub_dir / "child.toml"
        child.write_text('extends = "common.toml"\nwidth = 33.0\n', encoding="utf-8")

        inst = load_config_file(child, ConfigTestBox, configs_dir=cfg_dir)
        self.assertEqual(inst.length, 77.0)
        self.assertEqual(inst.width, 33.0)

    def test_missing_parent_raises_config_error(self):
        (self.root / "orphan.toml").write_text('extends = "nonexistent.toml"\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "orphan.toml", ConfigTestBox)
        msg = str(ctx.exception).lower()
        self.assertTrue("not found" in msg or "does not exist" in msg)

    def test_invalid_extends_directive_raises_config_error(self):
        (self.root / "bad_extends.toml").write_text("extends = 12345\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "bad_extends.toml", ConfigTestBox)
        self.assertIn("Invalid 'extends'", str(ctx.exception))

    def test_extends_empty_string_raises_config_error(self):
        (self.root / "empty_str.toml").write_text('extends = ""\nlength = 15.0\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "empty_str.toml", ConfigTestBox)
        self.assertIn("Invalid 'extends'", str(ctx.exception))

    def test_extends_empty_list_raises_config_error(self):
        (self.root / "empty_list.toml").write_text("extends = []\nlength = 15.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "empty_list.toml", ConfigTestBox)
        self.assertIn("Invalid 'extends'", str(ctx.exception))

    def test_extends_falsy_boolean_raises_config_error(self):
        (self.root / "bool_false.toml").write_text("extends = false\nlength = 15.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "bool_false.toml", ConfigTestBox)
        self.assertIn("Invalid 'extends'", str(ctx.exception))

    def test_directives_stripped(self):
        (self.root / "base.toml").write_text("width = 15.0\n", encoding="utf-8")
        (self.root / "child.toml").write_text(
            'extends = "base.toml"\n'
            'model = "ConfigTestBox"\n'
            '"$schema" = "https://example.com/schema.json"\n'
            'length = 22.0\n',
            encoding="utf-8",
        )
        inst = load_config_file(self.root / "child.toml", ConfigTestBox)
        self.assertEqual(inst.length, 22.0)
        self.assertEqual(inst.width, 15.0)

    def test_deep_merge_nested_submodels(self):
        (self.root / "base.toml").write_text("[spec]\nsub_width = 8.0\nsub_height = 12.0\n", encoding="utf-8")
        (self.root / "child.toml").write_text('extends = "base.toml"\n[spec]\nsub_width = 9.5\n', encoding="utf-8")
        inst = load_config_file(self.root / "child.toml", ConfigTestBox)
        self.assertEqual(inst.spec.sub_width, 9.5)
        self.assertEqual(inst.spec.sub_height, 12.0)


class TestConfigCycleDetection(unittest.TestCase):
    """Test circular extends loop detection and error diagnostics."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_self_referencing_loop(self):
        f = self.root / "loop.toml"
        f.write_text('extends = "loop.toml"\nlength = 20.0\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        self.assertIn("Circular", str(ctx.exception))
        self.assertIn("loop.toml -> loop.toml", str(ctx.exception))

    def test_two_hop_loop(self):
        (self.root / "a.toml").write_text('extends = "b.toml"\n', encoding="utf-8")
        (self.root / "b.toml").write_text('extends = "a.toml"\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "a.toml", ConfigTestBox)
        self.assertIn("Circular", str(ctx.exception))

    def test_three_hop_loop(self):
        (self.root / "a.toml").write_text('extends = "b.toml"\n', encoding="utf-8")
        (self.root / "b.toml").write_text('extends = "c.toml"\n', encoding="utf-8")
        (self.root / "c.toml").write_text('extends = "a.toml"\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(self.root / "a.toml", ConfigTestBox)
        self.assertIn("Circular", str(ctx.exception))
        self.assertIn("a.toml -> b.toml -> c.toml -> a.toml", str(ctx.exception))


class TestConfigTypoDiagnostics(unittest.TestCase):
    """Test fuzzy typo matching via difflib and enriched error reporting."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_typo_fuzzy_suggestion(self):
        f = self.root / "typo.toml"
        f.write_text("wal_gap = 12.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("wall_gap", msg)
        self.assertIn("did you mean", msg.lower())

    def test_suffix_variation_suggestion(self):
        f = self.root / "suffix.toml"
        f.write_text("visor_drop_height = 15.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("visor_drop", msg)

    def test_domain_synonym_suggestion(self):
        f = self.root / "synonym.toml"
        f.write_text("glass_width = 12.8\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("wall_gap", msg)

    def test_unrelated_unknown_key_no_suggestion(self):
        f = self.root / "random.toml"
        f.write_text("zzzzz_unknown_parameter = 12345\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("zzzzz_unknown_parameter", msg)
        self.assertNotIn("did you mean", msg.lower())

    def test_multiple_typos_in_single_file(self):
        f = self.root / "multi.toml"
        f.write_text("wal_gap = 10.0\nvisr_drop = 15.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertTrue("wal_gap" in msg and "visr_drop" in msg)
        self.assertTrue("wall_gap" in msg and "visor_drop" in msg)

    def test_nested_submodel_typo_diagnostics(self):
        f = self.root / "sub_typo.toml"
        f.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("sub_width", msg)
        self.assertIn("spec:", msg)

    def test_pep_604_union_submodel_typo_suggestion(self):
        class ModernModel(ModelPrototype):
            length: float = 20.0
            spec: SubSpec | None = None
            def build(self): pass

        f = self.root / "pep604.toml"
        f.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ModernModel)
        msg = str(ctx.exception)
        self.assertIn("sub_width", msg)
        self.assertIn("spec:", msg)

    def test_multi_submodel_union_pep604_typo_suggestion(self):
        class MultiPEP604Model(ModelPrototype):
            length: float = 20.0
            spec: SubSpec | AltSubSpec | None = None
            def build(self): pass

        f1 = self.root / "pep604_subspec.toml"
        f1.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx1:
            load_config_file(f1, MultiPEP604Model)
        self.assertIn("sub_width", str(ctx1.exception))
        self.assertIn("spec.SubSpec:", str(ctx1.exception))

        f2 = self.root / "pep604_altsubspec.toml"
        f2.write_text("[spec]\nalt_diametr = 5.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx2:
            load_config_file(f2, MultiPEP604Model)
        self.assertIn("alt_diameter", str(ctx2.exception))
        self.assertIn("spec.AltSubSpec:", str(ctx2.exception))

    def test_multi_submodel_union_typing_union_typo_suggestion(self):
        class MultiTypingUnionModel(ModelPrototype):
            length: float = 20.0
            spec: Union[SubSpec, AltSubSpec, None] = None
            def build(self): pass

        f1 = self.root / "typing_subspec.toml"
        f1.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx1:
            load_config_file(f1, MultiTypingUnionModel)
        self.assertIn("sub_width", str(ctx1.exception))
        self.assertIn("spec.SubSpec:", str(ctx1.exception))

        f2 = self.root / "typing_altsubspec.toml"
        f2.write_text("[spec]\nalt_diametr = 5.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx2:
            load_config_file(f2, MultiTypingUnionModel)
        self.assertIn("alt_diameter", str(ctx2.exception))
        self.assertIn("spec.AltSubSpec:", str(ctx2.exception))

    def test_multi_submodel_union_required_typo_suggestion(self):
        class MultiRequiredUnionModel(ModelPrototype):
            length: float = 20.0
            spec: SubSpec | AltSubSpec = SubSpec()
            def build(self): pass

        f = self.root / "req_subspec.toml"
        f.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, MultiRequiredUnionModel)
        self.assertIn("sub_width", str(ctx.exception))

    def test_multi_submodel_union_primitive_mix_typo_suggestion(self):
        class MultiPrimitiveMixModel(ModelPrototype):
            length: float = 20.0
            spec: SubSpec | int | None = None
            def build(self): pass

        f = self.root / "prim_subspec.toml"
        f.write_text("[spec]\nsub_widht = 8.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, MultiPrimitiveMixModel)
        self.assertIn("sub_width", str(ctx.exception))

    def test_multi_submodel_union_nested_in_hierarchy_typo_suggestion(self):
        class LevelOuter(BaseModel):
            model_config = {"extra": "forbid"}
            spec: SubSpec | AltSubSpec | None = None

        class MultiNestedHierarchyModel(ModelPrototype):
            outer: LevelOuter = LevelOuter()
            def build(self): pass

        f = self.root / "nested_subspec.toml"
        f.write_text("[outer.spec]\nalt_diametr = 5.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, MultiNestedHierarchyModel)
        msg = str(ctx.exception)
        self.assertIn("outer.spec.AltSubSpec:", msg)
        self.assertIn("alt_diameter", msg)

    def test_short_key_id_does_not_match_width(self):
        candidates = ["width", "length", "height"]
        self.assertIsNone(get_typo_suggestion("id", candidates))

    def test_short_candidates_do_not_match_arbitrary_words(self):
        short_cands = ["x", "y", "z"]
        for word in ["xenon", "xml", "xyz"]:
            with self.subTest(word=word):
                self.assertIsNone(get_typo_suggestion(word, short_cands))

    def test_cross_field_validation_error_preserved(self):
        f = self.root / "cross_field.toml"
        f.write_text("wall_thickness = 4.0\nvisor_drop = 6.0\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config_file(f, ConfigTestBox)
        msg = str(ctx.exception)
        self.assertIn("visor_drop must be at least 2x wall_thickness", msg)


class TestConfigScaffolding(unittest.TestCase):
    """Test TOML configuration templating and scaffolding utilities."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_config_template_content(self):
        tmpl = create_config_template(ConfigTestBox, schema_url="../schemas/Box.schema.json")
        self.assertIn("#:schema ../schemas/Box.schema.json", tmpl)
        self.assertIn("length = 20.0", tmpl)
        self.assertIn("[mm]", tmpl)

    def test_create_config_template_plain_basemodel(self):
        class PlainModel(BaseModel):
            """Plain model without Param."""
            foo: str = "bar"
            num: int = 42

        tmpl = create_config_template(PlainModel)
        self.assertIn("foo = \"bar\"", tmpl)
        self.assertIn("num = 42", tmpl)

    def test_create_config_template_plain_basemodel_comprehensive(self):
        class PlainModel(BaseModel):
            """Plain model without ModelPrototype."""
            req_bare: str
            req_field: int = Field(..., description="Required int\nwith multiline description")
            req_nullable: str | None = Field(...)
            opt_str: str | None = None
            opt_int: int | None = Field(default=None, description="Optional integer")
            count: int = 42
            rate: float = 3.1415
            flag_true: bool = True
            flag_false: bool = False
            tags: list[str] = ["alpha", "beta"]
            data_list: list[int] = Field(default_factory=list)
            data_dict: dict[str, Any] = Field(default_factory=dict)

        tmpl = create_config_template(PlainModel, schema_url="https://example.com/schema.json")
        self.assertNotIn("PydanticUndefined", tmpl)
        self.assertIn("# req_bare = <required>", tmpl)
        self.assertIn("# req_field = <required>", tmpl)
        self.assertIn("# req_nullable = <required>", tmpl)
        self.assertIn("# opt_str = <optional>", tmpl)
        self.assertIn("# opt_int = <optional>", tmpl)
        self.assertIn("count = 42", tmpl)
        self.assertIn("rate = 3.1415", tmpl)
        self.assertIn("flag_true = true", tmpl)
        self.assertIn("flag_false = false", tmpl)
        self.assertIn('tags = ["alpha", "beta"]', tmpl)
        self.assertIn("data_list = []", tmpl)
        self.assertIn("data_dict = {}", tmpl)

        parsed = tomllib.loads(tmpl)
        self.assertNotIn("req_bare", parsed)
        self.assertNotIn("req_field", parsed)
        self.assertNotIn("opt_str", parsed)
        self.assertEqual(parsed["count"], 42)
        self.assertEqual(parsed["rate"], 3.1415)
        self.assertTrue(parsed["flag_true"])
        self.assertFalse(parsed["flag_false"])
        self.assertEqual(parsed["tags"], ["alpha", "beta"])
        self.assertEqual(parsed["data_list"], [])
        self.assertEqual(parsed["data_dict"], {})

    def test_scaffold_config_writes_file(self):
        dest = self.root / "configs" / "test.toml"
        p = scaffold_config(ConfigTestBox, dest)
        self.assertEqual(p, dest)
        self.assertTrue(dest.is_file())
        self.assertIn("length = 20.0", dest.read_text(encoding="utf-8"))

    def test_scaffold_config_appends_toml_suffix(self):
        dest = self.root / "configs" / "variant"
        p = scaffold_config(ConfigTestBox, dest)
        self.assertEqual(p.suffix, ".toml")
        self.assertTrue(p.is_file())

    def test_scaffold_config_case_insensitive_suffix(self):
        dest_upper = self.root / "configs" / "variant.TOML"
        p1 = scaffold_config(ConfigTestBox, dest_upper)
        self.assertEqual(p1.name, "variant.TOML")
        self.assertTrue(p1.is_file())

        dest_mixed = self.root / "configs" / "variant2.Toml"
        p2 = scaffold_config(ConfigTestBox, dest_mixed)
        self.assertEqual(p2.name, "variant2.Toml")
        self.assertTrue(p2.is_file())

    def test_scaffold_config_fails_if_exists_without_force(self):
        dest = self.root / "exists.toml"
        dest.write_text("existing content\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            scaffold_config(ConfigTestBox, dest, force=False)

    def test_scaffold_config_overwrites_with_force(self):
        dest = self.root / "exists.toml"
        dest.write_text("existing content\n", encoding="utf-8")
        scaffold_config(ConfigTestBox, dest, force=True)
        self.assertIn("length = 20.0", dest.read_text(encoding="utf-8"))

    def test_scaffolded_file_round_trips_through_load_config(self):
        dest = self.root / "roundtrip.toml"
        scaffold_config(ConfigTestBox, dest)
        inst = load_config_file(dest, ConfigTestBox)
        self.assertEqual(inst.length, 20.0)
        self.assertEqual(inst.width, 10.0)
        self.assertEqual(inst.wall_gap, 9.625)
        self.assertEqual(inst.handedness, "left")


class TestConfigDiscoverAndRead(unittest.TestCase):
    """Test discovery and raw reading of configuration files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_discover_config_files(self):
        cfg_dir = self.root / "configs"
        cfg_dir.mkdir()
        (cfg_dir / "b.toml").write_text("x = 1\n", encoding="utf-8")
        (cfg_dir / "a.toml").write_text("x = 2\n", encoding="utf-8")
        (cfg_dir / "ignore.txt").write_text("ignored\n", encoding="utf-8")

        discovered = discover_config_files(cfg_dir)
        self.assertEqual([p.name for p in discovered], ["a.toml", "b.toml"])

    def test_discover_nonexistent_dir_returns_empty(self):
        discovered = discover_config_files(self.root / "nonexistent")
        self.assertEqual(discovered, [])

    def test_read_config_file_directives_flag(self):
        f = self.root / "test.toml"
        f.write_text('model = "Test"\nlength = 10.0\n', encoding="utf-8")

        raw = read_config_file(f, strip_directives=False)
        self.assertIn("model", raw)
        self.assertIn("length", raw)

        clean = read_config_file(f, strip_directives=True)
        self.assertNotIn("model", clean)
        self.assertIn("length", clean)


if __name__ == "__main__":
    unittest.main()
