"use strict"
// A logo for a provider or a model's maker. The core names the brand (a slug);
// this file draws it: the bundled monochrome logo when there is one, otherwise a
// monogram, so a list never has holes in it.

;(function defineProviders(root) {
  const K = (root.K = root.K || {})

  // Files in renderer/providers/ (test/providers.test.js keeps this list honest).
  const LOGOS = new Set([
    "01ai", "ai21", "anthropic", "baichuan", "baidu", "baseten", "bytedance", "cerebras", "cohere",
    "databricks", "deepinfra", "deepseek", "fireworks", "friendli", "github", "google", "groq", "iflytek",
    "meta", "minimax", "mistral", "modelscope", "moonshot", "nebius", "novita", "nvidia", "openai",
    "openrouter", "perplexity", "qwen", "sambanova", "scaleway", "stepfun", "tencent", "together", "vercel",
    "xai", "xiaomi", "zai", "zhipu",
  ])

  function monogram(label) {
    const words = String(label || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/).filter(Boolean)
    const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)
    return letters.toUpperCase()
  }

  // A hue from the slug, so the same brand always gets the same tile.
  function hue(text) {
    let value = 0
    for (const char of String(text)) value = (value * 31 + char.codePointAt(0)) % 360
    return value
  }

  // K.brand("anthropic", "Anthropic", 20) -> a span of that size.
  K.brand = function brand(slug, label, size = 20) {
    const node = document.createElement("span")
    node.className = "brand"
    node.style.width = `${size}px`
    node.style.height = `${size}px`
    if (LOGOS.has(slug)) {
      node.classList.add("is-logo")
      const url = `url(providers/${slug}.svg)`
      node.style.webkitMaskImage = url
      node.style.maskImage = url
      node.setAttribute("role", "img")
      node.setAttribute("aria-label", label || slug)
      return node
    }
    node.classList.add("is-monogram")
    node.textContent = monogram(label || slug)
    node.style.fontSize = `${Math.round(size * 0.46)}px`
    node.style.backgroundColor = `hsl(${hue(slug || label)} 28% 30%)`
    node.setAttribute("aria-hidden", "true")
    return node
  }

  K.LOGOS = LOGOS
  K.monogram = monogram

  if (typeof module === "object" && module.exports) module.exports = { LOGOS, monogram }
})(typeof window !== "undefined" ? window : globalThis)
