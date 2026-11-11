"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")

const { LOGOS, monogram } = require("../renderer/js/providers.js")

const DIRECTORY = path.join(__dirname, "../renderer/providers")

test("the logo list and the files in renderer/providers agree", () => {
  const files = fs.readdirSync(DIRECTORY).filter((name) => name.endsWith(".svg")).map((name) => name.slice(0, -4)).sort()
  assert.deepEqual([...LOGOS].sort(), files)
})

test("a logo is a plain monochrome SVG with a viewBox", () => {
  for (const slug of LOGOS) {
    const svg = fs.readFileSync(path.join(DIRECTORY, `${slug}.svg`), "utf8")
    assert.match(svg, /viewBox="0 0 24 24"/, slug)
    assert.doesNotMatch(svg, /<script|onload=|href=/i, slug)
  }
})

test("a monogram takes the initials of the first two words, or two letters of one", () => {
  assert.equal(monogram("Together AI"), "TA")
  assert.equal(monogram("Groq"), "GR")
  assert.equal(monogram("01.AI (Yi)"), "0A")
  assert.equal(monogram("ByteDance Doubao (Volcengine Ark)"), "BD")
  assert.equal(monogram(""), "?")
})

test("every brand the core names is either drawn or gets a monogram", () => {
  // The slugs kernel/config/brand_icons.py can return; a slug with no file must still render.
  const brands = ["anthropic", "openai", "google", "deepseek", "groq", "cohere", "anymodel", ""]
  for (const slug of brands) assert.ok(LOGOS.has(slug) || monogram(slug || "?").length >= 1)
})
