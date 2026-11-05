import assert from "node:assert/strict";
import { test } from "node:test";
import { checkContrast, ratio } from "../tools/contrast.mjs";
import { checkLocales } from "../tools/i18n-check.mjs";

test("both locales have the same keys", () => {
  assert.deepEqual(checkLocales(new URL("../src/i18n/", import.meta.url)), []);
});

test("every text token clears WCAG AA in both themes", () => {
  assert.deepEqual(checkContrast(new URL("../src/tokens.css", import.meta.url)).failures, []);
});

test("contrast math", () => {
  assert.equal(ratio("#000000", "#ffffff").toFixed(1), "21.0");
});
