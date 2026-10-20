# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from math_text import replace_math, tex_to_text


@pytest.mark.parametrize(
    ("tex", "text"),
    [
        (r"\sqrt{6,646,021}\approx2577{,}9878", "√6,646,021≈2577,9878"),
        (r"2586\cdot2570+1=(2578+8)(2578-8)+1", "2586·2570+1=(2578+8)(2578-8)+1"),
        (r"\sqrt{a+b}", "√(a+b)"),
        (r"\sqrt[3]{x}", "³√x"),
        (r"2578^2-8^2", "2578²-8²"),
        (r"x_{10}^{-1}", "x₁₀⁻¹"),
        (r"e^{x+y}", "e^(x+y)"),
        (r"\frac{1}{2}", "1/2"),
        (r"\frac{a+b}{c}", "(a+b)/c"),
        (r"\boxed{\sqrt{2}\approx1.41}", "[ √2≈1.41 ]"),
        (r"\left(\frac{a}{b}\right)", "(a/b)"),
        (r"\mathbf{x}+\text{ cm}", "x+ cm"),
        (r"\alpha\to\infty", "α→∞"),
        (r"\unknowncommand{x}", r"\unknowncommand{x}"),
    ],
)
def test_tex_to_text(tex: str, text: str) -> None:
    assert tex_to_text(tex) == text


def test_the_four_delimiters_and_display_formulas_on_their_own_lines() -> None:
    assert replace_math(r"so \(2^2\) and $3^2$ here") == "so 2² and 3² here"
    assert replace_math("a\n\\[\n\\sqrt{9}=3\n\\]\nb") == "a\n\n  √9=3\n\nb"
    assert replace_math("$$x^2$$") == "\n\n  x²\n\n"


def test_money_escaped_dollars_code_spans_and_fences_are_not_math() -> None:
    assert replace_math("costs $5 and $6 today") == "costs $5 and $6 today"
    assert replace_math(r"a \$b\$ c") == r"a \$b\$ c"
    assert replace_math("use `$x^2$` literally") == "use `$x^2$` literally"
    assert replace_math("```tex\n\\[x^2\\]\n```\n$y^2$") == "```tex\n\\[x^2\\]\n```\ny²"
    assert replace_math("```tex\n\\[x^2\\]") == "```tex\n\\[x^2\\]"


def test_a_formula_still_arriving_stays_as_written_until_it_closes() -> None:
    assert replace_math(r"so \[x^2") == r"so \[x^2"
