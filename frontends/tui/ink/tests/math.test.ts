import assert from "node:assert/strict"
import { test } from "node:test"

import { replaceMath, texToText } from "../math.js"
import { parseBlocks } from "../ui/markdown.js"

test("texToText: roots, products and approximations read as text", () => {
  assert.equal(texToText("\\sqrt{6,646,021}\\approx2577{,}9878"), "√6,646,021≈2577,9878")
  assert.equal(texToText("2586\\cdot2570+1=(2578+8)(2578-8)+1"), "2586·2570+1=(2578+8)(2578-8)+1")
  assert.equal(texToText("\\sqrt{a+b}"), "√(a+b)")
  assert.equal(texToText("\\sqrt[3]{x}"), "³√x")
})

test("texToText: powers and indices become Unicode when every character has one", () => {
  assert.equal(texToText("2578^2-8^2"), "2578²-8²")
  assert.equal(texToText("x_{10}^{-1}"), "x₁₀⁻¹")
  assert.equal(texToText("e^{x+y}"), "e^(x+y)")
  assert.equal(texToText("a_{k}"), "aₖ")
})

test("texToText: fractions parenthesise only what is not a single word", () => {
  assert.equal(texToText("\\frac{1}{2}"), "1/2")
  assert.equal(texToText("\\frac{a+b}{c}"), "(a+b)/c")
})

test("texToText: styling commands, boxes and \\left\\right keep the content", () => {
  assert.equal(texToText("\\boxed{\\sqrt{2}\\approx1.41}"), "[ √2≈1.41 ]")
  assert.equal(texToText("\\left(\\frac{a}{b}\\right)"), "(a/b)")
  assert.equal(texToText("\\mathbf{x}+\\text{ cm}"), "x+ cm")
  assert.equal(texToText("\\alpha\\to\\infty"), "α→∞")
})

test("texToText: an unknown command is left as written", () => {
  assert.equal(texToText("\\unknowncommand{x}"), "\\unknowncommand{x}")
})

test("replaceMath: the four delimiters, display ones on their own lines", () => {
  assert.equal(replaceMath("so \\(2^2\\) and $3^2$ here"), "so 2² and 3² here")
  assert.equal(replaceMath("a\n\\[\n\\sqrt{9}=3\n\\]\nb"), "a\n\n  √9=3\n\nb")
  assert.equal(replaceMath("$$x^2$$"), "\n\n  x²\n\n")
})

test("replaceMath: money, escaped dollars, code spans and fences are not math", () => {
  assert.equal(replaceMath("costs $5 and $6 today"), "costs $5 and $6 today")
  assert.equal(replaceMath("a \\$b\\$ c"), "a \\$b\\$ c")
  assert.equal(replaceMath("use `$x^2$` literally"), "use `$x^2$` literally")
  assert.equal(replaceMath("```tex\n\\[x^2\\]\n```\n$y^2$"), "```tex\n\\[x^2\\]\n```\ny²")
  assert.equal(replaceMath("```tex\n\\[x^2\\]"), "```tex\n\\[x^2\\]")
})

test("replaceMath: a formula still arriving stays as written until it closes", () => {
  assert.equal(replaceMath("so \\[x^2"), "so \\[x^2")
})

test("parseBlocks: the formula from a model answer is plain paragraphs, not stray brackets", () => {
  const blocks = parseBlocks("поэтому\n\\[\n\\boxed{\\sqrt{6646021}\\approx2577{,}9878}.\n\\]\n\nответ не целый")
  const text = blocks.flatMap((b) => (b.kind === "para" ? [b.text.trim()] : []))
  assert.deepEqual(text, ["поэтому", "[ √6646021≈2577,9878 ].", "ответ не целый"])
})
