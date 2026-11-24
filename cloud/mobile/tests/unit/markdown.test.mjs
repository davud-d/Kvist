import assert from "node:assert/strict";
import { test } from "node:test";
import { inline, parse } from "../../js/markdown.js";

test("paragraphs, headings and lists", () => {
  const blocks = parse("# Title\n\nSome **bold** text\nsecond line\n\n- one\n- two\n\n1. first\n2. second");
  assert.deepEqual(blocks.map((b) => b.type), ["h", "p", "ul", "ol"]);
  assert.equal(blocks[2].items.length, 2);
  assert.equal(blocks[3].items.length, 2);
});

test("a fenced block keeps its text exactly and knows whether it closed", () => {
  const closed = parse("```js\nconst a = 1;\n  indented\n```\nafter")[0];
  assert.equal(closed.type, "code");
  assert.equal(closed.lang, "js");
  assert.equal(closed.text, "const a = 1;\n  indented");
  assert.equal(closed.closed, true);
  const open = parse("```py\nprint(1)")[0];
  assert.equal(open.closed, false);
  assert.equal(open.text, "print(1)");
});

test("markup inside code is not interpreted", () => {
  const block = parse("```\n**not bold** <b>x</b>\n```")[0];
  assert.equal(block.text, "**not bold** <b>x</b>");
  assert.deepEqual(inline("`a *b* c`"), [{ code: "a *b* c" }]);
});

test("only http, https and mailto links are links", () => {
  assert.deepEqual(inline("[x](https://a.b/c)"), [{ link: "https://a.b/c", label: "x" }]);
  const bad = inline("[x](javascript:alert(1))");
  assert.ok(!bad.some((span) => "link" in span));
  const bare = inline("see https://example.com/a.");
  assert.equal(bare.find((s) => "link" in s).link, "https://example.com/a");
});

test("quotes nest and rules split", () => {
  const blocks = parse("> quoted\n> more\n\n---\n\nend");
  assert.deepEqual(blocks.map((b) => b.type), ["quote", "rule", "p"]);
  assert.equal(blocks[0].blocks[0].type, "p");
});

test("a half-typed stream never throws", () => {
  const text = "Here is **bold and `code";
  for (let i = 0; i <= text.length; i++) assert.doesNotThrow(() => parse(text.slice(0, i)));
});
