// A tiny element builder. Text is always set as text, never as HTML.

export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split(".");
  const node = document.createElement(name || "div");
  if (classes.length) node.className = classes.join(" ");
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = [node.className, value].filter(Boolean).join(" ");
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "text") node.textContent = value;
    else if (key in node && typeof value !== "string") node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.firstChild.remove();
  return node;
}

export function replace(node, ...children) {
  clear(node);
  return append(node, children);
}

export function skeleton(lines = 3) {
  return h("div", { "aria-hidden": "true" }, Array.from({ length: lines }, (_, i) =>
    h("span.skeleton.line", { style: `width:${90 - i * 17}%` })));
}

/** A dialog in the page: focus moves in, Escape and the backdrop close it, focus goes back. */
export function modal(build) {
  const opener = document.activeElement;
  let scrim;
  const close = () => {
    scrim.remove();
    document.removeEventListener("keydown", onKey);
    if (opener && opener.focus) opener.focus();
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  const box = h("div.modal", { role: "dialog", "aria-modal": "true" });
  scrim = h("div.modal-scrim", { onclick: (event) => { if (event.target === scrim) close(); } }, box);
  append(box, [build(close)]);
  const title = box.querySelector("h2");
  if (title) {
    title.id = "modal-title";
    box.setAttribute("aria-labelledby", "modal-title");
  }
  document.body.append(scrim);
  document.addEventListener("keydown", onKey);
  (box.querySelector("[autofocus]") || box.querySelector("button, input, textarea"))?.focus();
  return close;
}
