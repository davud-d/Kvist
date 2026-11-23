import assert from "node:assert/strict";
import { test } from "node:test";

import { lang, t } from "../js/i18n.js";
import { modelFamily, modelName } from "../js/models.js";

test("ids read as the names their vendors use", () => {
  const cases = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6.2-sol": "GPT-6.2 Sol",
    "gpt-5.4-mini": "GPT-5.4 Mini",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
    "gemini-4-neon": "Gemini 4 Neon",
    "models/gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
    "deepseek-v4.1-flash-1017": "DeepSeek V4.1 Flash",
    "deepseek-flash": "DeepSeek Flash",
    "glm-5.3": "GLM-5.3",
    "qwen/qwen3.6-27b": "Qwen3.6 27B",
    "k3.1-pro": "K3.1 Pro",
    "kimi-for-coding": "Kimi for Coding",
    "mistral-large-latest": "Mistral Large",
    "fake-model": "Fake Model",
  };
  for (const [id, name] of Object.entries(cases)) assert.equal(modelName(id), name, id);
  assert.equal(modelName(""), "");
});

test("a family is found by its name inside the id, the narrower one first", () => {
  assert.equal(modelFamily("claude-opus-5-5").short, "Opus");
  assert.equal(modelFamily("anthropic/claude-fable-5-2").short, "Fable");
  assert.equal(modelFamily("gpt-6.2-sol").short, "Sol");
  assert.equal(modelFamily("deepseek-v4.1-flash-1017").lines[0], "model line deepseek flash 1");
  assert.equal(modelFamily("deepseek-v4-pro").lines[0], "model line deepseek 1");
  assert.equal(modelFamily("glm-5.3-flash").lines[0], "model line glm flash 1");
  // "sol" only as a word of its own, not inside another one.
  assert.equal(modelFamily("console-model"), null);
  assert.equal(modelFamily("fake-model"), null);
  assert.equal(modelFamily(""), null);
});

test("every line a family can show is written in both languages", () => {
  const ids = ["claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-6-astra", "gpt-6.2-sol",
    "gpt-6-luna", "gemini-4-carbon", "gemini-4-neon", "gemini-4-argon", "gemini-4-hydrogen", "deepseek-flash", "deepseek-v4-pro",
    "glm-5.3-flash", "glm-5.3"];
  const keys = [...ids.flatMap((id) => modelFamily(id).lines), "model line kvist picks 1", "model line kvist picks 2"];
  for (const language of ["ru", "en"]) {
    lang.current = language;
    for (const key of keys) assert.notEqual(t(key), key, `${language}: ${key}`);
  }
  lang.current = "en";
});
