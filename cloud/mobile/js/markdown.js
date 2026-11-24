// A small Markdown reader for what a coding agent writes: paragraphs, headings, lists, quotes, fenced code, inline
// code, bold, italic, links. It makes a tree of plain objects (parse) and then DOM nodes (render); nothing is ever
// put in with innerHTML, so the model's text cannot add markup to the page.
//
// While a reply streams, a code fence that has not closed yet is shown as plain code: the text inside it is not
// highlighted or reflowed until the closing line arrives, which is what keeps a streaming answer from flickering.

const LINK = /^(https?:\/\/|mailto:)/i;

/** @returns {Array<object>} blocks: {type: "p"|"h"|"ul"|"ol"|"quote"|"code"|"rule", ...} */
export function parse(source) {
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[i])) body.push(lines[i++]);
      const closed = i < lines.length;
      i++;
      blocks.push({ type: "code", lang: fence[2], text: body.join("\n"), closed });
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      blocks.push({ type: "h", level: heading[1].length, inline: inline(heading[2].replace(/\s+#+\s*$/, "")) });
      i++;
      continue;
    }
    if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ type: "rule" });
      i++;
      continue;
    }
    if (/^\s{0,3}>/.test(line)) {
      const quoted = [];
      while (i < lines.length && /^\s{0,3}>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s{0,3}>\s?/, ""));
      blocks.push({ type: "quote", blocks: parse(quoted.join("\n")) });
      continue;
    }
    const item = line.match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/);
    if (item) {
      const ordered = /\d/.test(item[2]);
      const items = [];
      while (i < lines.length) {
        const next = lines[i].match(/^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/);
        if (next && /\d/.test(next[2]) === ordered) {
          items.push([next[3]]);
          i++;
        } else if (items.length && /^\s{2,}\S/.test(lines[i])) {
          items.at(-1).push(lines[i++].trim());
        } else break;
      }
      blocks.push({ type: ordered ? "ol" : "ul", items: items.map((parts) => inline(parts.join(" "))) });
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !/^\s{0,3}(`{3,}|~{3,}|#{1,4}\s|>)/.test(lines[i])
      && !/^(\s*)([-*+]|\d{1,3}[.)])\s+/.test(lines[i])) paragraph.push(lines[i++].trim());
    blocks.push({ type: "p", inline: inline(paragraph.join("\n")) });
  }
  return blocks;
}

/** Inline spans: `code`, **bold**, *italic*, [text](url), and bare https links. */
export function inline(text) {
  const out = [];
  let rest = String(text);
  const pattern = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|(?<![\w*])\*([^*\n]+)\*(?![\w*])|\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<>)\]]+[^\s<>)\].,;:!?])/;
  while (rest) {
    const m = pattern.exec(rest);
    if (!m) {
      out.push({ text: rest });
      break;
    }
    if (m.index) out.push({ text: rest.slice(0, m.index) });
    if (m[1] !== undefined) out.push({ code: m[1] });
    else if (m[2] !== undefined) out.push({ bold: inline(m[2]) });
    else if (m[3] !== undefined) out.push({ italic: inline(m[3]) });
    else if (m[4] !== undefined) out.push(LINK.test(m[5]) ? { link: m[5], label: m[4] } : { text: m[0] });
    else out.push({ link: m[6], label: m[6] });
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

const el = (tag, props, ...children) => {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props ?? {})) node.setAttribute(name, value);
  node.append(...children.flat().filter((child) => child !== null && child !== undefined));
  return node;
};

function renderInline(spans, onLink) {
  return spans.map((span) => {
    if ("code" in span) return el("code", {}, span.code);
    if ("bold" in span) return el("strong", {}, ...renderInline(span.bold, onLink));
    if ("italic" in span) return el("em", {}, ...renderInline(span.italic, onLink));
    if ("link" in span) {
      const a = el("a", { href: span.link, rel: "noopener noreferrer", target: "_blank" }, span.label);
      a.addEventListener("click", (event) => {
        event.preventDefault();
        onLink(span.link);
      });
      return a;
    }
    // A single newline inside a paragraph is a line break: the model writes short lines on purpose.
    const lines = span.text.split("\n");
    return lines.flatMap((line, index) => (index ? [document.createElement("br"), line] : [line]));
  }).flat();
}

function renderCode(block, copy) {
  const button = el("button", { type: "button", class: "code-copy", "aria-label": "Copy" }, "Copy");
  button.addEventListener("click", () => copy(block.text, button));
  return el("figure", { class: `code${block.closed ? "" : " open"}` },
    el("figcaption", {}, el("span", {}, block.lang || "text"), button),
    el("pre", {}, el("code", {}, block.text)));
}

/** DOM for parsed blocks. `onLink(url)` and `copy(text, button)` come from the page (the app opens links outside). */
export function render(blocks, { onLink, copy }) {
  return blocks.map((block) => {
    switch (block.type) {
      case "h": return el(`h${Math.min(block.level + 2, 6)}`, {}, ...renderInline(block.inline, onLink));
      case "ul": return el("ul", {}, ...block.items.map((spans) => el("li", {}, ...renderInline(spans, onLink))));
      case "ol": return el("ol", {}, ...block.items.map((spans) => el("li", {}, ...renderInline(spans, onLink))));
      case "quote": return el("blockquote", {}, ...render(block.blocks, { onLink, copy }));
      case "code": return renderCode(block, copy);
      case "rule": return el("hr");
      default: return el("p", {}, ...renderInline(block.inline, onLink));
    }
  });
}
