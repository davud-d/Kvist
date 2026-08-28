# -*- coding: utf-8 -*-
"""Loader for opencode-format ``theme.json`` colour themes.

Kvist reads the opencode theme schema (https://opencode.ai/docs/themes/) so that
third-party themes (tokyonight, everforest, catppuccin, gruvbox, nord) load
unmodified. A theme file has an optional flat ``defs`` colour dictionary and a
``theme`` dictionary of 50 named slots; each slot value is a colour, an ANSI
number, a ``defs`` reference, ``"none"``, or a ``{"dark", "light"}`` pair.

The module only ever produces plain strings. It never imports ``rich`` and pulls
a single lookup table out of ``prompt_toolkit`` — that keeps it independent of
any particular UI, which is the point of shipping it before the second terminal
frontend exists.

NOTE: ``kvist.spec`` bundles only ``('prompts', 'prompts')`` into the PyInstaller
binary. Until ``('themes', 'themes')`` is added there, a frozen build ships
without the built-in themes and ``load_theme`` cannot find ``kvist``. Wiring that
into the spec belongs to the task that connects this module to a UI.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from prompt_toolkit.output.vt100 import FG_ANSI_COLORS

__all__ = [
    "BUILTIN_THEME_NAME",
    "SLOT_NAMES",
    "Mode",
    "Theme",
    "ThemeError",
    "ThemeIssue",
    "discover_themes",
    "load_available_themes",
    "load_theme",
    "theme_directories",
    "to_prompt_toolkit",
    "to_rich",
]

Mode = Literal["dark", "light"]

BUILTIN_THEME_NAME = "kvist"

SLOT_NAMES: tuple[str, ...] = (
    "primary", "secondary", "accent", "error", "warning", "success", "info",
    "text", "textMuted",
    "background", "backgroundPanel", "backgroundElement",
    "border", "borderActive", "borderSubtle",
    "diffAdded", "diffRemoved", "diffContext", "diffHunkHeader",
    "diffHighlightAdded", "diffHighlightRemoved", "diffAddedBg", "diffRemovedBg",
    "diffContextBg", "diffLineNumber", "diffAddedLineNumberBg",
    "diffRemovedLineNumberBg",
    "markdownText", "markdownHeading", "markdownLink", "markdownLinkText",
    "markdownCode", "markdownBlockQuote", "markdownEmph", "markdownStrong",
    "markdownHorizontalRule", "markdownListItem", "markdownListEnumeration",
    "markdownImage", "markdownImageText", "markdownCodeBlock",
    "syntaxComment", "syntaxKeyword", "syntaxFunction", "syntaxVariable",
    "syntaxString", "syntaxNumber", "syntaxType", "syntaxOperator",
    "syntaxPunctuation",
)

if len(set(SLOT_NAMES)) != len(SLOT_NAMES) or len(SLOT_NAMES) != 50:
    raise RuntimeError("SLOT_NAMES must list exactly 50 unique opencode slots")

_SLOT_SET = frozenset(SLOT_NAMES)

_MODULE_DIR = Path(__file__).resolve().parent
_BUILTIN_THEME_DIR = _MODULE_DIR / "themes"
_BUILTIN_KVIST_PATH = _BUILTIN_THEME_DIR / "kvist.json"
_USER_THEME_SUBPATH = Path(".kvist") / "themes"

_HEX6_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
_INT_RE = re.compile(r"-?\d+\Z")

_COLOR_FORMS_HELP = (
    "expected '#rrggbb', an ANSI number 0-255, an ANSI colour name, "
    "'none', or a defs key"
)


def _ansi_name_to_index() -> dict[str, int]:
    """Derive the ANSI name -> index 0-15 map from prompt_toolkit's own table.

    A hard-coded list would drift on a prompt_toolkit upgrade; this stays in
    sync. ``ansidefault`` (SGR 39) has no index and is handled one branch
    earlier, so it never reaches this map.
    """
    mapping: dict[str, int] = {}
    for name, code in FG_ANSI_COLORS.items():
        if 30 <= code <= 37:
            mapping[name.lower()] = code - 30
        elif 90 <= code <= 97:
            mapping[name.lower()] = code - 90 + 8
    return mapping


_ANSI_NAME_TO_INDEX = _ansi_name_to_index()
_INDEX_TO_ANSI_NAME = {index: name for name, index in _ANSI_NAME_TO_INDEX.items()}


def _xterm256_to_hex(index: int) -> str:
    """xterm-256 colour cube / grey ramp -> ``#rrggbb`` (prompt_toolkit has no
    256-colour input, so indexes >= 16 are expanded to truecolour there)."""
    if 16 <= index <= 231:
        offset = index - 16
        levels = (0, 95, 135, 175, 215, 255)
        red = levels[offset // 36]
        green = levels[(offset // 6) % 6]
        blue = levels[offset % 6]
    else:
        grey = (index - 232) * 10 + 8
        red = green = blue = grey
    return f"#{red:02x}{green:02x}{blue:02x}"


def to_prompt_toolkit(colour: str) -> str:
    """Canonical colour -> a string ``prompt_toolkit.styles.Style`` accepts."""
    if colour == "none":
        return "ansidefault"
    if colour.startswith("#"):
        return colour
    index = int(colour)
    if index < 16:
        return _INDEX_TO_ANSI_NAME[index]
    return _xterm256_to_hex(index)


def to_rich(colour: str) -> str:
    """Canonical colour -> a string ``rich.color.Color.parse`` accepts."""
    if colour == "none":
        return "default"
    if colour.startswith("#"):
        return colour
    return f"color({int(colour)})"


class ThemeError(Exception):
    """A theme file (or a requested theme name) is unusable."""


@dataclass(frozen=True, slots=True)
class ThemeIssue:
    """A non-fatal problem: an ignored slot, an inherited slot, a skipped file."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class Theme:
    name: str
    path: Path
    dark: Mapping[str, str]
    light: Mapping[str, str]
    inherited: frozenset[str]
    issues: tuple[ThemeIssue, ...]

    def color(self, slot: str, mode: Mode) -> str:
        if mode not in ("dark", "light"):
            raise ValueError(f"mode must be 'dark' or 'light', got {mode!r}")
        table = self.dark if mode == "dark" else self.light
        try:
            return table[slot]
        except KeyError:
            raise KeyError(f"unknown theme slot {slot!r}") from None

    def prompt_toolkit_color(self, slot: str, mode: Mode) -> str:
        return to_prompt_toolkit(self.color(slot, mode))

    def rich_color(self, slot: str, mode: Mode) -> str:
        return to_rich(self.color(slot, mode))


def _resolve_flat(value: object, defs: Mapping[str, object], trail: list[str]) -> str:
    """Resolve one flat colour value (no dark/light object) to canonical form."""
    if isinstance(value, bool):
        raise ThemeError(f"colour value must not be a boolean: {value!r}")
    if isinstance(value, int):
        if not 0 <= value <= 255:
            raise ThemeError(f"ANSI colour {value} is out of range 0-255")
        return str(value)
    if isinstance(value, float):
        raise ThemeError(f"colour value must not be a float: {value!r}")
    if not isinstance(value, str):
        raise ThemeError(
            f"colour value must be a string or number, got {type(value).__name__}"
        )

    if value in defs:
        if value in trail:
            raise ThemeError(f"cyclic defs reference: {' -> '.join([*trail, value])}")
        return _resolve_flat(defs[value], defs, [*trail, value])

    if _HEX6_RE.match(value):
        return value.lower()

    lowered = value.lower()
    if _INT_RE.match(value):
        number = int(value)
        if not 0 <= number <= 255:
            raise ThemeError(f"ANSI colour {number} is out of range 0-255")
        return str(number)
    if lowered == "ansidefault":
        return "none"
    if lowered in _ANSI_NAME_TO_INDEX:
        return str(_ANSI_NAME_TO_INDEX[lowered])
    if lowered == "none":
        return "none"

    hint = " hex colours must be '#rrggbb'" if value.startswith("#") else ""
    raise ThemeError(f"unresolved colour {value!r}: {_COLOR_FORMS_HELP}{hint}")


def _canonical_slot(raw: object, defs: Mapping[str, object], mode: Mode) -> str:
    if isinstance(raw, dict):
        keys = set(raw)
        if keys != {"dark", "light"}:
            raise ThemeError(
                f"slot object must have exactly 'dark' and 'light', got {sorted(keys)}"
            )
        branch = raw[mode]
        if isinstance(branch, dict):
            raise ThemeError("slot 'dark'/'light' branch must be a flat colour")
        return _resolve_flat(branch, defs, [])
    return _resolve_flat(raw, defs, [])


def _parse_document(
    doc: object, path: Path
) -> tuple[dict[str, str], dict[str, str], list[ThemeIssue]]:
    if not isinstance(doc, dict):
        raise ThemeError(f"{path}: theme file root must be a JSON object")
    raw_theme = doc.get("theme")
    if not isinstance(raw_theme, dict):
        raise ThemeError(f"{path}: missing required 'theme' object")
    raw_defs = doc.get("defs", {})
    if not isinstance(raw_defs, dict):
        raise ThemeError(f"{path}: 'defs' must be an object")
    for key, entry in raw_defs.items():
        if isinstance(entry, dict):
            raise ThemeError(
                f"{path}: defs entry {key!r} must be a flat colour, not a dark/light object"
            )

    dark: dict[str, str] = {}
    light: dict[str, str] = {}
    issues: list[ThemeIssue] = []
    for slot, raw_value in raw_theme.items():
        if slot not in _SLOT_SET:
            issues.append(ThemeIssue(path, f"unknown slot {slot!r} ignored"))
            continue
        try:
            dark[slot] = _canonical_slot(raw_value, raw_defs, "dark")
            light[slot] = _canonical_slot(raw_value, raw_defs, "light")
        except ThemeError as error:
            raise ThemeError(f"{path}: slot {slot!r}: {error}") from error
    return dark, light, issues


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ThemeError(f"cannot read theme file {path}: {error}") from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ThemeError(f"invalid JSON in {path}: {error}") from error


def _builtin_maps() -> tuple[dict[str, str], dict[str, str]]:
    dark, light, _ = _parse_document(_read_json(_BUILTIN_KVIST_PATH), _BUILTIN_KVIST_PATH)
    missing = sorted(_SLOT_SET.difference(dark))
    if missing:
        raise ThemeError(
            f"built-in theme {_BUILTIN_KVIST_PATH} is missing slots: {missing}"
        )
    return dark, light


def _load_theme_file(name: str, path: Path) -> Theme:
    is_builtin = path.resolve() == _BUILTIN_KVIST_PATH
    dark, light, issues = _parse_document(_read_json(path), path)
    missing = sorted(_SLOT_SET.difference(dark))

    if is_builtin:
        if missing:
            raise ThemeError(f"built-in theme {path} is missing slots: {missing}")
        inherited: frozenset[str] = frozenset()
    else:
        fallback_dark, fallback_light = _builtin_maps()
        for slot in missing:
            dark[slot] = fallback_dark[slot]
            light[slot] = fallback_light[slot]
            issues.append(
                ThemeIssue(path, f"slot {slot!r} inherited from built-in theme")
            )
        inherited = frozenset(missing)

    return Theme(
        name=name,
        path=path,
        dark=MappingProxyType({slot: dark[slot] for slot in SLOT_NAMES}),
        light=MappingProxyType({slot: light[slot] for slot in SLOT_NAMES}),
        inherited=inherited,
        issues=tuple(issues),
    )


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def theme_directories(
    workspace: Path | str | None = None, cwd: Path | str | None = None
) -> list[Path]:
    """Theme search roots, lowest priority first (later entries win on name).

    1. ``<module_dir>/themes`` — built-in
    2. ``~/.kvist/themes`` — user (kept next to the existing ``~/.kvist/config.json``)
    3. ``<workspace>/.kvist/themes``
    4. ``<cwd>/.kvist/themes`` (dropped if it is the same directory as 3)
    """
    base = Path.cwd() if cwd is None else Path(cwd)
    candidates = [_BUILTIN_THEME_DIR, Path.home() / _USER_THEME_SUBPATH]
    if workspace is not None:
        candidates.append(Path(workspace) / _USER_THEME_SUBPATH)
    candidates.append(base / _USER_THEME_SUBPATH)

    resolved: list[Path] = []
    seen: set[Path] = set()
    for directory in candidates:
        key = _resolve(directory)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(key)
    return resolved


def discover_themes(
    workspace: Path | str | None = None, cwd: Path | str | None = None
) -> dict[str, Path]:
    """Map theme name (file stem) -> winning file path across the search roots."""
    found: dict[str, Path] = {}
    for directory in theme_directories(workspace, cwd):
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob("*.json")):
            if file.is_file():
                found[file.stem] = file
    return found


def load_theme(
    name: str = BUILTIN_THEME_NAME,
    *,
    workspace: Path | str | None = None,
    cwd: Path | str | None = None,
) -> Theme:
    """Load one theme by name. Raises ``ThemeError`` if it is unknown or broken."""
    available = discover_themes(workspace, cwd)
    path = available.get(name)
    if path is None:
        listed = ", ".join(sorted(available)) or "none"
        raise ThemeError(f"Unknown theme {name!r}. Available: {listed}.")
    return _load_theme_file(name, path)


def load_available_themes(
    *, workspace: Path | str | None = None, cwd: Path | str | None = None
) -> tuple[list[Theme], list[ThemeIssue]]:
    """Load every discoverable theme; broken ones are skipped with an issue."""
    themes: list[Theme] = []
    issues: list[ThemeIssue] = []
    for name, path in sorted(discover_themes(workspace, cwd).items()):
        try:
            themes.append(_load_theme_file(name, path))
        except ThemeError as error:
            issues.append(ThemeIssue(path, str(error)))
    return themes, issues
