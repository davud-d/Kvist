// A provider's mark, as the web workspace's model picker draws it: its logo (Simple Icons, CC0) in its brand colour on a
// neutral tile, a globe for the person's own endpoint, or the first letter when there is no logo.

import { h, icon } from "./dom.js";
import { PROVIDER_MARKS } from "./web/provider-icons.js";

// Brand colours; a brand that is black or white itself is left out and follows the text colour.
const COLORS = {
  anthropic: "#e07a4f", deepseek: "#4d6bfe", qwen: "#8f6bff", moonshot: "#3b82f6", kimi: "#3b82f6",
  xiaomi: "#ff6900", "xiaomi-tokenplan": "#ff6900", minimax: "#ff4f5e", qianfan: "#2d5bff", modelscope: "#8b5cf6",
  mistral: "#ff8205", perplexity: "#22b8cf", databricks: "#ff5f46", gemini: "#4c8bf5", doubao: "#2dd4ff",
  scaleway: "#b57aff",
};

const SVG = "http://www.w3.org/2000/svg";

export function providerMark(id, label = "", size = 14) {
  const tile = h("span", { class: "mark-tile", "aria-hidden": "true", style: { "--size": `${size}px` } });
  const path = PROVIDER_MARKS[id];
  if (path) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("fill", COLORS[id] ?? "currentColor");
    const shape = document.createElementNS(SVG, "path");
    shape.setAttribute("d", path);
    svg.append(shape);
    tile.append(svg);
  } else if (id === "other") {
    tile.append(icon("globe", size));
  } else {
    tile.textContent = (label || id).replace(/^[^A-Za-zА-Яа-я0-9]+/, "").slice(0, 1).toUpperCase();
  }
  return tile;
}
