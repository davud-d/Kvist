// Vendored from cloud/workspace/js/models.js (the web workspace): model ids as people read them.
// Model ids as people read them. A provider's id ("claude-opus-5-5", "gpt-6-astra", "deepseek-v4.1-flash-1017")
// is turned into a name ("Claude Opus 5.5", "GPT-6 Astra", "DeepSeek V4.1 Flash") by rules, not by a table of every
// model, so a model released tomorrow reads right too. The name is only for reading: the id is what is saved and
// sent, and the page keeps showing it next to the name, so nothing is hidden and nothing is guessed.
//
// A few families also have a short name and a line for the home screen: what the model is good for, and when a
// cheaper or stronger one would serve better. No prices: they change every month, the advice does not.

// Brands keep their own spelling; the ones in DASHED join their version with a dash ("GPT-6", "GLM-5.3").
const BRANDS = {
  claude: "Claude", gpt: "GPT", gemini: "Gemini", gemma: "Gemma", deepseek: "DeepSeek", glm: "GLM", qwen: "Qwen",
  qwq: "QwQ", kimi: "Kimi", grok: "Grok", mistral: "Mistral", codestral: "Codestral", devstral: "Devstral",
  ministral: "Ministral", magistral: "Magistral", minimax: "MiniMax", llama: "Llama", mimo: "MiMo", ernie: "ERNIE",
  doubao: "Doubao", command: "Command", sonar: "Sonar", phi: "Phi", nemotron: "Nemotron", seed: "Seed",
};
const DASHED = new Set(["gpt", "glm"]);
const SMALL = new Set(["for", "with", "of", "and"]);
const ACRONYMS = { oss: "OSS", vl: "VL", ai: "AI", hd: "HD", tts: "TTS", ocr: "OCR", moe: "MoE", it: "IT" };

/** The id without a vendor path, a "models/" prefix, a release date or "latest": none of them names the model. */
function bareId(id) {
  return String(id)
    .trim()
    .replace(/^.*\//, "")
    .replace(/[-_](latest|\d{8}|\d{4}-\d{2}-\d{2}|\d{4})$/i, "");
}

function word(token) {
  const lower = token.toLowerCase();
  if (BRANDS[lower]) return BRANDS[lower];
  if (ACRONYMS[lower]) return ACRONYMS[lower];
  if (SMALL.has(lower)) return lower;
  // A brand with its version stuck on keeps it stuck on, as the vendor writes it ("Qwen3.8").
  const glued = lower.match(/^([a-z]+)(\d[\d.]*)$/);
  if (glued && BRANDS[glued[1]]) return `${BRANDS[glued[1]]}${glued[2]}`;
  // Sizes and context lengths: "120b" → "120B", "256k" → "256K".
  if (/^\d+(\.\d+)?[bkm]$/.test(lower)) return lower.toUpperCase();
  // Version letters: "v4.1" → "V4.1", "k3" → "K3", "r1" → "R1".
  if (/^[a-z]\d/.test(lower)) return lower.toUpperCase();
  if (/^\d/.test(lower)) return lower;
  return lower[0].toUpperCase() + lower.slice(1);
}

/** A model id as a name to read; an id the rules cannot read is returned as it is. */
export function modelName(id) {
  if (!id) return "";
  const tokens = bareId(id).split(/[-_\s]+/).filter(Boolean);
  if (!tokens.length) return String(id);
  // Version numbers split by dashes are one version: "claude-opus-5-5" is Opus 5.5.
  const merged = [];
  for (const token of tokens) {
    const previous = merged[merged.length - 1];
    if (/^\d+$/.test(token) && previous !== undefined && /^\d+(\.\d+)*$/.test(previous)) merged[merged.length - 1] = `${previous}.${token}`;
    else merged.push(token);
  }
  const words = [];
  merged.forEach((token, i) => {
    if (token === null) return;
    const lower = token.toLowerCase();
    const next = merged[i + 1];
    if (DASHED.has(lower) && next) {
      words.push(`${BRANDS[lower]}-${word(next)}`);
      merged[i + 1] = null;
      return;
    }
    words.push(word(token));
  });
  return words.filter(Boolean).join(" ");
}

// Families, the first match wins, so a narrower one ("deepseek … flash") stands before the wider ("deepseek").
// `short` is how the home question names the model; `line` starts the i18n keys of what it says under the question
// ("model line opus 1" … "model line opus {variants}"), so the home screen does not repeat one joke every visit.
const FAMILIES = [
  { test: /fable/, short: "Fable", line: "model line fable", variants: 3 },
  { test: /opus/, short: "Opus", line: "model line opus", variants: 3 },
  { test: /sonnet/, short: "Sonnet", line: "model line sonnet", variants: 3 },
  { test: /haiku/, short: "Haiku", line: "model line haiku", variants: 3 },
  { test: /astra/, short: "Astra", line: "model line astra", variants: 3 },
  { test: /(^|[^a-z])sol([^a-z]|$)/, short: "Sol", line: "model line sol", variants: 3 },
  { test: /luna/, short: "Luna", line: "model line luna", variants: 3 },
  { test: /carbon/, short: "Carbon", line: "model line carbon", variants: 3 },
  { test: /neon/, short: "Neon", line: "model line neon", variants: 2 },
  { test: /argon/, short: "Argon", line: "model line argon", variants: 2 },
  { test: /hydrogen/, short: "Hydrogen", line: "model line hydrogen", variants: 2 },
  { test: /deepseek.*flash|flash.*deepseek/, short: "DeepSeek Flash", line: "model line deepseek flash", variants: 3 },
  { test: /deepseek/, short: "DeepSeek", line: "model line deepseek", variants: 2 },
  { test: /glm.*(flash|air)/, short: "GLM", line: "model line glm flash", variants: 2 },
  { test: /glm/, short: "GLM", line: "model line glm", variants: 2 },
];

/** The family a model id belongs to ({short, lines}), or null for a model the page has nothing to say about. */
export function modelFamily(id) {
  if (!id) return null;
  const lower = bareId(id).toLowerCase();
  const found = FAMILIES.find((family) => family.test.test(lower));
  return found ? { short: found.short, lines: Array.from({ length: found.variants }, (_, i) => `${found.line} ${i + 1}`) } : null;
}
