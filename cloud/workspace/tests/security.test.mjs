import assert from "node:assert/strict";
import test from "node:test";
import { renderMarkdown, safeUrl } from "../js/markdown.js";

const attacks = [
  '<img src=x onerror="globalThis.__xss=1">',
  '<svg onload="globalThis.__xss=1">',
  '</script><script>globalThis.__xss=1</script>',
  '[click](javascript:alert(1))', '[click](data:text/html,test)',
  '[click](java\u0000script:alert(1))',
  '[click](https://example.com/`onmouseover=alert(1)`)',
  '\u00000\u0000 \u00010\u0001',
];
for (const attack of attacks) test(`markdown treats attack as text: ${JSON.stringify(attack)}`, () => {
  const html = renderMarkdown(attack);
  assert.doesNotMatch(html, /<(?:script|img|svg|iframe)\b/i);
  assert.doesNotMatch(html, /href="(?:javascript|data):/i);
  assert.doesNotMatch(html, /href="[^">]*<code/i);
  assert.doesNotMatch(html, /undefined/);
});
test("links reject parser placeholder characters", () => {
  for (const code of [0, 1, 9, 10, 13, 31, 127]) assert.equal(safeUrl(`https://example.com/${String.fromCharCode(code)}x`), null);
});
