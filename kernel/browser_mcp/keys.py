# -*- coding: utf-8 -*-
"""Key names the model writes ("Enter", "Ctrl+L") and what each protocol needs to press them."""
from __future__ import annotations

from dataclasses import dataclass

# name: (DOM key, DOM code, Windows virtual key code, text the key types, WebDriver key value)
SPECIAL = {
    "enter": ("Enter", "Enter", 13, "\r", ""),
    "tab": ("Tab", "Tab", 9, "", ""),
    "escape": ("Escape", "Escape", 27, "", ""),
    "backspace": ("Backspace", "Backspace", 8, "", ""),
    "delete": ("Delete", "Delete", 46, "", ""),
    "space": (" ", "Space", 32, " ", " "),
    "arrowup": ("ArrowUp", "ArrowUp", 38, "", ""),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, "", ""),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, "", ""),
    "arrowright": ("ArrowRight", "ArrowRight", 39, "", ""),
    "home": ("Home", "Home", 36, "", ""),
    "end": ("End", "End", 35, "", ""),
    "pageup": ("PageUp", "PageUp", 33, "", ""),
    "pagedown": ("PageDown", "PageDown", 34, "", ""),
    "f5": ("F5", "F5", 116, "", ""),
}
ALIASES = {"esc": "escape", "return": "enter", "up": "arrowup", "down": "arrowdown", "left": "arrowleft",
           "right": "arrowright", "del": "delete", "pgup": "pageup", "pgdn": "pagedown"}
# name: (DOM key, DOM code, CDP modifier bit, WebDriver key value)
MODIFIERS = {
    "alt": ("Alt", "AltLeft", 1, ""),
    "ctrl": ("Control", "ControlLeft", 2, ""),
    "meta": ("Meta", "MetaLeft", 4, ""),
    "shift": ("Shift", "ShiftLeft", 8, ""),
}
MODIFIER_ALIASES = {"control": "ctrl", "cmd": "meta", "super": "meta", "win": "meta", "option": "alt"}


@dataclass(frozen=True, slots=True)
class Key:
    key: str
    code: str
    vk: int
    text: str
    webdriver: str


@dataclass(frozen=True, slots=True)
class Chord:
    modifiers: tuple[str, ...]
    key: Key


def char_key(char: str) -> Key:
    """A key that types one character, the way a keyboard layout would report it."""
    upper = char.upper()
    if "A" <= upper <= "Z":
        return Key(char, "Key" + upper, ord(upper), char, char)
    if "0" <= char <= "9":
        return Key(char, "Digit" + char, ord(char), char, char)
    if char == "\n":
        return named_key("enter")
    if char == " ":
        return named_key("space")
    return Key(char, "", 0, char, char)


def named_key(name: str) -> Key:
    key, code, vk, text, webdriver = SPECIAL[name]
    return Key(key, code, vk, text, webdriver)


def parse_chord(spec: str) -> Chord:
    """``"Ctrl+Shift+T"`` -> modifiers and the main key; raises ValueError for unknown names."""
    parts = [part.strip() for part in spec.replace(" + ", "+").split("+")]
    if spec.endswith("++"):
        parts = parts[:-2] + ["+"]
    if not parts or not parts[-1]:
        raise ValueError(f"empty key in {spec!r}")
    modifiers = []
    for part in parts[:-1]:
        name = MODIFIER_ALIASES.get(part.lower(), part.lower())
        if name not in MODIFIERS:
            raise ValueError(f"unknown modifier {part!r}; use Ctrl, Shift, Alt or Meta")
        modifiers.append(name)
    main = parts[-1]
    lowered = ALIASES.get(main.lower(), main.lower())
    if lowered in SPECIAL:
        return Chord(tuple(modifiers), named_key(lowered))
    if len(main) != 1:
        raise ValueError(f"unknown key {main!r}; use one character or a name like Enter, Tab, Escape, ArrowDown")
    # "Ctrl+A" means the A key; only Shift makes the letter a capital.
    char = main.upper() if "shift" in modifiers else main.lower()
    return Chord(tuple(modifiers), char_key(char))
