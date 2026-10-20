# -*- coding: utf-8 -*-
"""Turns written text into text a Russian voice can read.

The Silero model reads only Cyrillic words. A digit, a symbol or a Latin word is
dropped without a sound: "3 файла в 15:30" is heard as "файла в", and "config.py"
as noise. So everything the model cannot read is written out first: numbers,
times and percentages in words, file names and domains by how they are said,
Latin names the way they sound, symbols by their names.

Numbers are cardinals in the masculine nominative; "один" and "два" take the
feminine or neuter form before the common nouns listed in FEMININE and NEUTER.
Cases are not tracked: "в 2026 году" is read "в две тысячи двадцать шесть году",
which is understood, where a dropped number is not.
"""
from __future__ import annotations

import re

UNITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
# (one, few, many) for each thousand step, and whether its word is feminine.
STEPS = [("", "", "", False), ("тысяча", "тысячи", "тысяч", True), ("миллион", "миллиона", "миллионов", False),
         ("миллиард", "миллиарда", "миллиардов", False)]
FEMININE_UNITS = {1: "одна", 2: "две"}
NEUTER_UNITS = {1: "одно", 2: "два"}
# Stems of common nouns that follow a number: "1 минута", "2 строки", "1 окно".
FEMININE = ("минут", "секунд", "строк", "ошибк", "недел", "страниц", "папк", "запис", "задач", "копи", "ссылк",
            "команд", "функци", "переменн", "проверк", "версия", "версии", "сотн", "тысяч")
NEUTER = ("окн", "слов", "сообщени", "значени", "изменени", "совпадени", "дел", "место", "мест")


def plural(n: int, one: str, few: str, many: str) -> str:
    """The form of a noun that follows the number `n`."""
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def _below_thousand(n: int, feminine: bool, neuter: bool = False) -> list[str]:
    words = [HUNDREDS[n // 100]] if n >= 100 else []
    n %= 100
    if n >= 20:
        words.append(TENS[n // 10])
        n %= 10
        if n:
            words.append(_unit(n, feminine, neuter))
    elif n:
        words.append(_unit(n, feminine, neuter))
    return [word for word in words if word]


def _unit(n: int, feminine: bool, neuter: bool) -> str:
    if feminine and n in FEMININE_UNITS:
        return FEMININE_UNITS[n]
    if neuter and n in NEUTER_UNITS:
        return NEUTER_UNITS[n]
    return UNITS[n]


def cardinal(n: int, feminine: bool = False, neuter: bool = False) -> str:
    """`n` in words (up to the billions); "один"/"два" agree with the noun when told."""
    if n == 0:
        return UNITS[0]
    groups: list[int] = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    words: list[str] = []
    for step in range(len(groups) - 1, -1, -1):
        group = groups[step]
        if not group:
            continue
        one, few, many, step_feminine = STEPS[step] if step < len(STEPS) else STEPS[-1]
        if step and group == 1:
            words.append(one)  # "тысяча", "миллион": not "одна тысяча"
            continue
        words += _below_thousand(group, step_feminine if step else feminine, neuter if step == 0 else False)
        if step:
            words.append(plural(group, one, few, many))
    return " ".join(words)


def _agrees(following: str) -> tuple[bool, bool]:
    word = following.lower()
    return word.startswith(FEMININE), word.startswith(NEUTER)


def _decimal(match: re.Match[str]) -> str:
    whole, fraction = match.group(1), match.group(2)
    return f"{cardinal(int(whole))} точка {' '.join(cardinal(int(d)) for d in fraction)}"


def _clock(match: re.Match[str]) -> str:
    hours, minutes = int(match.group(1)), match.group(2)
    if minutes == "00":
        return f"{cardinal(hours)} ноль-ноль"
    spoken = (f"ноль {cardinal(int(minutes))}" if minutes.startswith("0") else cardinal(int(minutes)))
    return f"{cardinal(hours)} {spoken}"


def _percent(match: re.Match[str]) -> str:
    whole = int(match.group(1))
    if match.group(2):
        digits = " ".join(cardinal(int(d)) for d in match.group(2))
        return f"{cardinal(whole)} точка {digits} процента"
    return f"{cardinal(whole)} {plural(whole, 'процент', 'процента', 'процентов')}"


# How letters are named in Russian, for what cannot be pronounced as a word.
LETTER_NAMES = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
    "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл-ю", "x": "икс", "y": "уай", "z": "зед",
}
# Names people say often are spelled the way they are said.
KNOWN_WORDS = {
    "youtube": "ютуб", "fiverr": "файверр", "kvist": "квист", "google": "гугл", "github": "гитхаб",
    "gitlab": "гитлаб", "python": "пайтон", "linux": "линукс", "chrome": "хром", "firefox": "файрфокс",
    "telegram": "телеграм", "whatsapp": "вотсап", "discord": "дискорд", "spotify": "спотифай",
    "email": "имейл", "example": "экзампл", "gmail": "джимейл", "wifi": "вайфай", "openai": "оупенэйай", "deepseek": "дипсик",
    "claude": "клод", "chatgpt": "чатджипити", "ok": "окей", "okay": "окей", "hello": "хелло",
    "file": "файл", "config": "конфиг", "main": "мейн", "test": "тест", "tests": "тесты", "src": "эс-ар-си",
    "readme": "ридми", "json": "джейсон", "yaml": "ямл", "toml": "томл", "docker": "докер", "npm": "эн-пи-эм",
    "api": "эй-пи-ай", "cli": "си-эл-ай", "url": "ю-ар-эл", "ssh": "эс-эс-эйч", "sql": "эс-кью-эл",
    "html": "эйч-ти-эм-эл", "css": "си-эс-эс", "pdf": "пи-ди-эф", "png": "пи-эн-джи", "jpg": "джей-пег",
    "txt": "тэ-икс-тэ", "py": "пай", "md": "эм-ди", "js": "джей-эс", "ts": "ти-эс", "rs": "ар-эс", "sh": "эс-эйч",
    "com": "ком", "ru": "ру", "org": "орг", "net": "нет", "io": "ай-оу", "dev": "дев", "app": "эп", "ai": "эй-ай",
}
DIGRAPHS = {"sh": "ш", "ch": "ч", "th": "т", "ph": "ф", "ck": "к", "ee": "и", "oo": "у", "ya": "я", "yu": "ю", "yo": "ё"}
SOUNDS = dict(zip("abcdefghijklmnopqrstuvwxyz", ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о", "п", "к", "р", "с", "т", "у", "в", "в", "кс", "й", "з"]))
VOWELS = set("aeiouy")


def spell_latin(word: str) -> str:
    """A Latin word as Cyrillic: a known name; letter by letter when it has no vowel or is a short
    abbreviation in capitals; otherwise by its sounds."""
    lowered = word.lower()
    if lowered in KNOWN_WORDS:
        return KNOWN_WORDS[lowered]
    if not (set(lowered) & VOWELS) or (word.isupper() and len(word) <= 4):
        return "-".join(LETTER_NAMES[letter] for letter in lowered)
    out: list[str] = []
    i = 0
    while i < len(lowered):
        pair = lowered[i : i + 2]
        if pair in DIGRAPHS:
            out.append(DIGRAPHS[pair])
            i += 2
            continue
        out.append(SOUNDS[lowered[i]])
        i += 1
    return "".join(out)


SYMBOLS = {"/": " слэш ", "\\": " бэкслэш ", "&": " и ", "+": " плюс ", "=": " равно ", "#": " решётка ", "@": " собака ",
           "~": " тильда ", "->": " стрелка ", "=>": " стрелка ", "°": " градусов ", "_": " ",
           # Math, as the screen writes a formula (`math.rs`): a minus is U+2212, so "5-3" stays a range.
           "√": " корень из ", "≈": " приблизительно равно ", "·": " умножить на ", "×": " умножить на ",
           "÷": " разделить на ", "−": " минус ", "±": " плюс-минус ", "≠": " не равно ", "≤": " меньше или равно ",
           "≥": " больше или равно ", "→": " стрелка ", "∞": " бесконечность ", "π": " пи ", "∑": " сумма ",
           "∫": " интеграл ", "…": " "}
# Superscript digits as the screen writes a power: "x²" is "икс в квадрате".
SUPERSCRIPT = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+")
SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789−")
DROPPED = "`*|<>{}[]^\""
THOUSANDS = re.compile(r"\b\d{1,3}(?:[ \u00a0]\d{3})+\b")
FILE_NAME = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z][A-Za-z0-9]{0,4})\b")
VERSION = re.compile(r"\b([vV])?(\d+(?:\.\d+){2,})\b")
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
PERCENT = re.compile(r"\b(\d+)(?:[.,](\d+))?\s*%")
DECIMAL = re.compile(r"\b(\d+)[.,](\d+)\b")
NUMBER = re.compile(r"\b(\d+)\b(?:\s+([А-Яа-яЁё]+))?")
LATIN_WORD = re.compile(r"[A-Za-z]+")


def _file(match: re.Match[str]) -> str:
    return f"{spell_latin(match.group(1))} точка {spell_latin(match.group(2))}"


def _version(match: re.Match[str]) -> str:
    spoken = " точка ".join(cardinal(int(part)) for part in match.group(2).split("."))
    return f"ви {spoken}" if match.group(1) else spoken


def _power(match: re.Match[str]) -> str:
    exponent = match.group().translate(SUPERSCRIPT_DIGITS)
    named = {"2": " в квадрате ", "3": " в кубе "}
    return named.get(exponent, f" в степени {exponent} ")


def speakable_russian(text: str) -> str:
    """`text` with everything the voice cannot read written out in words."""
    # "1 000 000" is one number, not three.
    text = THOUSANDS.sub(lambda match: re.sub(r"[ \u00a0]", "", match.group()), text)
    text = SUPERSCRIPT.sub(_power, text)
    text = FILE_NAME.sub(_file, text)
    text = CLOCK.sub(_clock, text)
    text = PERCENT.sub(_percent, text)
    text = VERSION.sub(_version, text)
    text = DECIMAL.sub(_decimal, text)
    text = NUMBER.sub(_number_before_noun, text)
    for symbol in sorted(SYMBOLS, key=len, reverse=True):
        text = text.replace(symbol, SYMBOLS[symbol])
    text = "".join(" " if char in DROPPED else char for char in text)
    # Digits stuck to letters ("x86", "0xFF") are numbers too; the model would drop them.
    text = re.sub(r"\d+", lambda match: f" {cardinal(int(match.group()))} ", text)
    text = LATIN_WORD.sub(lambda match: spell_latin(match.group()), text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _number_before_noun(match: re.Match[str]) -> str:
    """A number in words; the noun after it stays in the text and is only looked at for agreement."""
    n = int(match.group(1))
    following = match.group(2)
    feminine, neuter = _agrees(following or "")
    spoken = cardinal(n, feminine, neuter)
    return f"{spoken} {following}" if following else spoken
