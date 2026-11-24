import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { dictionaries } from "../../js/i18n.js";

const JS = new URL("../../js/", import.meta.url).pathname;
const files = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? files(path) : path.endsWith(".js") ? [path] : [];
});

const { en, ru } = dictionaries;

test("both languages have exactly the same keys", () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ru).sort());
});

test("both say the same placeholders", () => {
  for (const key of Object.keys(en)) {
    const names = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    assert.deepEqual(names(ru[key]), names(en[key]), key);
  }
});

test("no phrase is empty and no Russian phrase is a leftover English one", () => {
  const SAME_OK = new Set(["plan start", "plan pro", "plan max", "plan remote", "method sber", "ok"]);
  for (const [key, text] of Object.entries(ru)) {
    assert.ok(text.trim(), key);
    if (!SAME_OK.has(key) && /^[\x00-\x7f]+$/.test(text) && !/^\{/.test(text)) assert.notEqual(text, en[key], `${key} is not translated`);
  }
});

test("every phrase the page asks for exists", () => {
  const missing = [];
  for (const file of files(JS)) {
    const source = readFileSync(file, "utf8");
    for (const [, key] of source.matchAll(/\bt\("([^"]+)"/g)) if (!(key in en)) missing.push(`${file.replace(JS, "")}: ${key}`);
    // Keys built from a stem and a value: every value the page can produce must exist.
    for (const [, key] of source.matchAll(/(?:text|say)\("([a-z ]+)"/g)) if (!(key in en) && /^(pay|banner|machine|pause)/.test(key)) missing.push(`${file.replace(JS, "")}: ${key}`);
  }
  assert.deepEqual(missing, []);
});

test("the keys built at run time are all there", () => {
  const families = {
    "machine ": ["none", "ready", "working", "waking", "asleep", "creating", "suspended", "updating", "off"],
    "day ": ["today", "yesterday", "week", "older"],
    "effort ": ["low", "medium", "high"],
    "plan ": ["trial", "start", "pro", "max", "remote"],
    "setup ": ["plan", "key", "project", "plan hint", "key hint", "project hint"],
    "key ": ["invalid_key", "no_funds", "rate_limited", "provider_down", "bad_endpoint", "model_not_found", "model_missing", "unknown_provider", "model_fixed"],
    "pause ": ["user", "hours_exhausted", "provider_unavailable", "invalid_key", "command_time_limit", "machine_restarted", "machine_sleeping", "machine_suspended", "core_updated", "agent_error"],
    "pay ": ["declined", "needs auth", "review", "too many", "already", "failed", "opened"],
    "method ": ["sbp", "erip", "card", "intl", "crypto", "sber"],
    "machine waking": [" long"], "machine creating": [" long"], "machine suspended": [" long"],
  };
  for (const [stem, tails] of Object.entries(families)) for (const tail of tails) assert.ok(`${stem}${tail}` in en, `${stem}${tail}`);
});
