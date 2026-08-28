# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from prompt_toolkit.styles import ANSI_COLOR_NAMES, Style
from rich.color import Color

import theme


# ── helpers ──────────────────────────────────────────────────────────────


def _full_theme(**slot_values: object) -> dict[str, object]:
    slots: dict[str, object] = {name: "#000000" for name in theme.SLOT_NAMES}
    slots.update(slot_values)
    return slots


def _write_theme(path: Path, *, theme_slots: object, defs: object | None = None) -> Path:
    doc: dict[str, object] = {}
    if defs is not None:
        doc["defs"] = defs
    doc["theme"] = theme_slots
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture
def user_themes(tmp_path, monkeypatch):
    """A private HOME and an empty cwd; returns the ~/.kvist/themes directory."""
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    themes_dir = home / ".kvist" / "themes"
    themes_dir.mkdir(parents=True)
    return SimpleNamespace(dir=themes_dir, cwd=cwd)


def _load(user_themes, name: str) -> theme.Theme:
    return theme.load_theme(name, cwd=user_themes.cwd)


# ── error handling: one case per <errors> bullet ─────────────────────────


def test_invalid_json(user_themes):
    (user_themes.dir / "bad.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(theme.ThemeError):
        _load(user_themes, "bad")


def test_missing_theme_key(user_themes):
    (user_themes.dir / "x.json").write_text(json.dumps({"defs": {}}), encoding="utf-8")
    with pytest.raises(theme.ThemeError, match="theme"):
        _load(user_themes, "x")


def test_reference_to_unknown_def(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="ghost"),
        defs={"real": "#111111"},
    )
    with pytest.raises(theme.ThemeError, match="ghost"):
        _load(user_themes, "x")


def test_cyclic_defs(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="a"),
        defs={"a": "b", "b": "a"},
    )
    with pytest.raises(theme.ThemeError, match="cyclic"):
        _load(user_themes, "x")


def test_hex_wrong_length(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="#12345"))
    with pytest.raises(theme.ThemeError, match="#rrggbb"):
        _load(user_themes, "x")


def test_hex_invalid_chars(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="#gggggg"))
    with pytest.raises(theme.ThemeError):
        _load(user_themes, "x")


def test_ansi_number_out_of_range(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary=256))
    with pytest.raises(theme.ThemeError, match="range"):
        _load(user_themes, "x")


def test_ansi_number_negative(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary=-1))
    with pytest.raises(theme.ThemeError, match="range"):
        _load(user_themes, "x")


def test_unknown_slot_is_non_fatal(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(**{"bogusSlot": "#123123"}),
    )
    loaded = _load(user_themes, "x")
    assert "bogusSlot" not in loaded.dark
    assert any("bogusSlot" in issue.message for issue in loaded.issues)


def test_missing_slot_is_inherited(user_themes):
    _write_theme(user_themes.dir / "partial.json", theme_slots={"primary": "#abcdef"})
    loaded = _load(user_themes, "partial")
    assert "background" in loaded.inherited
    assert loaded.color("background", "dark")  # resolves to a concrete colour
    assert any("inherited" in issue.message for issue in loaded.issues)


def test_unresolved_colour_message_names_the_string(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="burnt-sienna"))
    with pytest.raises(theme.ThemeError, match="burnt-sienna"):
        _load(user_themes, "x")


def test_hex_shorthand_gets_full_form_hint(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="#f0a"))
    with pytest.raises(theme.ThemeError, match="#rrggbb"):
        _load(user_themes, "x")


def test_unknown_theme_name(user_themes):
    with pytest.raises(theme.ThemeError) as info:
        theme.load_theme("typo", cwd=user_themes.cwd)
    assert "typo" in str(info.value)
    assert "kvist" in str(info.value)


def test_slot_object_missing_a_branch(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary={"dark": "#111111"}),
    )
    with pytest.raises(theme.ThemeError, match="dark.*light|light.*dark"):
        _load(user_themes, "x")


def test_defs_entry_may_not_be_dark_light_object(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="a"),
        defs={"a": {"dark": "#111111", "light": "#eeeeee"}},
    )
    with pytest.raises(theme.ThemeError):
        _load(user_themes, "x")


# ── value resolution ────────────────────────────────────────────────────


def test_def_reference_resolves(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="c"),
        defs={"c": "#abcdef"},
    )
    assert _load(user_themes, "x").color("primary", "dark") == "#abcdef"


def test_def_chain_resolves(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="a"),
        defs={"a": "b", "b": "#123456"},
    )
    assert _load(user_themes, "x").color("primary", "dark") == "#123456"


def test_dark_light_pair(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary={"dark": "#111111", "light": "#eeeeee"}),
    )
    loaded = _load(user_themes, "x")
    assert loaded.color("primary", "dark") == "#111111"
    assert loaded.color("primary", "light") == "#eeeeee"


def test_single_value_applies_to_both_modes(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="#abcabc"))
    loaded = _load(user_themes, "x")
    assert loaded.color("primary", "dark") == "#abcabc"
    assert loaded.color("primary", "light") == "#abcabc"


def test_none_value(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="none"))
    assert _load(user_themes, "x").color("primary", "dark") == "none"


def test_ansi_number_as_json_number(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary=5))
    assert _load(user_themes, "x").color("primary", "dark") == "5"


def test_ansi_number_as_string(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="5"))
    assert _load(user_themes, "x").color("primary", "dark") == "5"


def test_ansi_name_folds_to_index(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="ansiwhite"))
    assert _load(user_themes, "x").color("primary", "dark") == "15"


def test_ansidefault_folds_to_none(user_themes):
    _write_theme(user_themes.dir / "x.json", theme_slots=_full_theme(primary="ansidefault"))
    assert _load(user_themes, "x").color("primary", "dark") == "none"


def test_ansi_name_is_case_insensitive(user_themes):
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="ANSIWHITE", secondary="AnsiWhite"),
    )
    loaded = _load(user_themes, "x")
    assert loaded.color("primary", "dark") == "15"
    assert loaded.color("secondary", "dark") == "15"


def test_defs_key_wins_over_ansi_name_but_only_exact_case(user_themes):
    # defs keys are case-sensitive JSON keys; colour names are case-insensitive.
    # So "ansiwhite" is a defs reference (-> #000000, parse step 1) while
    # "ANSIWHITE" has no matching defs key and folds to the ANSI index
    # (-> "15", parse step 5).
    _write_theme(
        user_themes.dir / "x.json",
        theme_slots=_full_theme(primary="ansiwhite", secondary="ANSIWHITE"),
        defs={"ansiwhite": "#000000"},
    )
    loaded = _load(user_themes, "x")
    assert loaded.color("primary", "dark") == "#000000"
    assert loaded.color("secondary", "dark") == "15"


# ── ANSI name <-> index bijection ───────────────────────────────────────


def test_ansi_name_index_bijection():
    # The circle closes for all 17 names, through two different branches:
    #   16 indexed names:  name -> canon str(index) (step 5) -> pt -> name
    #   ansidefault:       name -> canon "none"     (step 4) -> pt -> "ansidefault"
    # Recording this keeps a future reader from "fixing" working code when the
    # ansidefault case takes the other path.
    derived = theme._ANSI_NAME_TO_INDEX
    assert set(derived) == set(ANSI_COLOR_NAMES) - {"ansidefault"}
    for name, index in derived.items():
        assert theme._resolve_flat(name, {}, []) == str(index)
        assert theme.to_prompt_toolkit(str(index)) == name
    assert theme._resolve_flat("ansidefault", {}, []) == "none"
    assert theme.to_prompt_toolkit("none") == "ansidefault"


# ── directory hierarchy ─────────────────────────────────────────────────


def test_theme_directories_order(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    workspace = tmp_path / "ws"
    cwd = tmp_path / "cwd"
    dirs = theme.theme_directories(workspace=workspace, cwd=cwd)
    assert dirs == [
        (Path(theme.__file__).resolve().parent / "themes"),
        (home / ".kvist" / "themes").resolve(),
        (workspace / ".kvist" / "themes").resolve(),
        (cwd / ".kvist" / "themes").resolve(),
    ]


def test_cwd_dir_deduped_against_workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    same = tmp_path / "both"
    dirs = theme.theme_directories(workspace=same, cwd=same)
    assert dirs.count((same / ".kvist" / "themes").resolve()) == 1


def test_higher_priority_dir_overrides_builtin(user_themes):
    _write_theme(
        user_themes.dir / "kvist.json",
        theme_slots=_full_theme(primary="#abcdef"),
    )
    loaded = _load(user_themes, "kvist")
    assert loaded.path == user_themes.dir / "kvist.json"
    assert loaded.color("primary", "dark") == "#abcdef"


def test_inherit_source_is_the_builtin_file_not_the_discovered_theme(user_themes):
    # A user kvist.json with a partial slot set must still fill the gaps from
    # <module_dir>/themes/kvist.json, not from itself.
    _write_theme(user_themes.dir / "kvist.json", theme_slots={"primary": "#abcdef"})
    loaded = _load(user_themes, "kvist")
    assert loaded.color("primary", "dark") == "#abcdef"
    assert "background" in loaded.inherited
    # an inherited slot carries the built-in file's value, not the user's
    module_builtin_dark, _ = theme._builtin_maps()
    assert loaded.color("background", "dark") == module_builtin_dark["background"]
    assert loaded.color("background", "dark") != "#abcdef"


def test_load_available_skips_broken_file(user_themes):
    _write_theme(user_themes.dir / "good.json", theme_slots=_full_theme())
    (user_themes.dir / "broken.json").write_text("{", encoding="utf-8")
    themes, issues = theme.load_available_themes(cwd=user_themes.cwd)
    names = {t.name for t in themes}
    assert "good" in names and "kvist" in names
    assert any(i.path == user_themes.dir / "broken.json" for i in issues)
    assert "broken" not in names


def test_works_with_no_user_directories(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    loaded = theme.load_theme(cwd=cwd)
    assert loaded.name == "kvist"
    assert len(loaded.dark) == 50


# ── conversion to library forms ─────────────────────────────────────────


def test_to_prompt_toolkit_forms():
    assert theme.to_prompt_toolkit("#4fc3f7") == "#4fc3f7"
    assert theme.to_prompt_toolkit("5") == "ansimagenta"
    assert theme.to_prompt_toolkit("180") == "#d7af87"
    assert theme.to_prompt_toolkit("none") == "ansidefault"


def test_to_rich_forms():
    assert theme.to_rich("#4fc3f7") == "#4fc3f7"
    assert theme.to_rich("5") == "color(5)"
    assert theme.to_rich("180") == "color(180)"
    assert theme.to_rich("none") == "default"


def test_converted_values_are_accepted_by_the_libraries(user_themes):
    _write_theme(
        user_themes.dir / "mix.json",
        theme_slots=_full_theme(primary="none", secondary="ansiwhite", accent=200),
    )
    for name in ("kvist", "mix"):
        loaded = _load(user_themes, name)
        for slot in theme.SLOT_NAMES:
            for mode in ("dark", "light"):
                pt = loaded.prompt_toolkit_color(slot, mode)
                Style.from_dict({"x": pt})
                Style.from_dict({"x": f"bg:{pt}"})
                Color.parse(loaded.rich_color(slot, mode))


# ── the built-in theme ──────────────────────────────────────────────────


def test_builtin_theme_fully_resolves(user_themes):
    loaded = _load(user_themes, "kvist")
    assert loaded.inherited == frozenset()
    assert loaded.issues == ()
    for slot in theme.SLOT_NAMES:
        for mode in ("dark", "light"):
            value = loaded.color(slot, mode)
            assert isinstance(value, str) and value


def test_slot_names_are_fifty_and_unique():
    assert len(theme.SLOT_NAMES) == 50
    assert len(set(theme.SLOT_NAMES)) == 50
