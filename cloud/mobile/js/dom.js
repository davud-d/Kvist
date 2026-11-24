// Tiny DOM helpers: h() builds an element, icon() an inline SVG, fill() swaps a node's children.
// Text always goes in as a text node; nothing here ever assigns innerHTML.

import { ICONS } from "./icons.js";

const SVG = "http://www.w3.org/2000/svg";

/** h("button", {class: "x", onclick: fn, aria: {label: "…"}, data: {id: 1}}, child, "text") */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
    else if (name === "aria") for (const [k, v] of Object.entries(value)) node.setAttribute(`aria-${k}`, String(v));
    else if (name === "data") for (const [k, v] of Object.entries(value)) node.dataset[k] = String(v);
    else if (name === "style" && typeof value === "object") for (const [k, v] of Object.entries(value)) node.style.setProperty(k, String(v));
    else if (value === true) node.setAttribute(name, "");
    else node.setAttribute(name, String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

/** A 24x24 line icon by name (icons.js); it takes its colour from the text around it. */
export function icon(name, size = 20) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (const d of ICONS[name] ?? ICONS.step) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export const $ = (selector, root = document) => root.querySelector(selector);
