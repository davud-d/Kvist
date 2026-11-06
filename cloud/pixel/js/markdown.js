// A small, safe Markdown renderer for what a model writes: paragraphs,
// headings, lists, quotes, fenced code, inline code, bold, italics and links.
// Everything is escaped first; the only tags in the output are ours.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Only web links; `javascript:` and friends stay text. */
export function safeUrl(url) {
  return /^https?:\/\/[^\s"'<>]+$/i.test(url) && !/[\u0000-\u001f\u007f]/.test(url) ? url : null;
}

function inline(source) {
  const codes = [];
  let text = source.replace(/`([^`\n]+)`/g, (_, code) => {
    codes.push(`<code class="md-code">${escapeHtml(code)}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });
  text = escapeHtml(text)
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (whole, label, href) => {
      const url = safeUrl(href.replace(/&amp;/g, "&"));
      return url ? `<a href="${escapeHtml(url)}" data-external>${label}</a>` : whole;
    })
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
}

function flushList(out, list) {
  if (!list) return;
  const items = list.items.map((item) => `<li>${inline(item)}</li>`).join("");
  out.push(`<${list.tag} class="md-list">${items}</${list.tag}>`);
}

let mathRenderer = null;

/** `fn(tex, display) -> html`; without one, formulas stay readable as TeX text. */
export function setMathRenderer(fn) {
  mathRenderer = fn;
}

function renderMath(tex, display) {
  let html = null;
  try {
    html = mathRenderer ? mathRenderer(tex, display) : null;
  } catch {
    html = null;
  }
  const tag = display ? "div" : "span";
  return html ?? `<${tag} class="${display ? "math-block" : "math"} raw">${escapeHtml(tex)}</${tag}>`;
}

const FORMULAS = [
  [/\$\$([\s\S]+?)\$\$/g, true],
  [/\\\[([\s\S]+?)\\\]/g, true],
  [/\\\(([\s\S]+?)\\\)/g, false],
  // `$x$` only when it hugs its content and is not followed by a digit, so prices stay prices.
  [/(?<![\\$\w])\$(?=\S)([^$\n]*?\S)\$(?![\w$])/g, false],
];

/** Swaps formulas for tokens (outside code) so Markdown never reads inside them. */
function takeFormulas(source, found) {
  return source
    .split(/(```[\s\S]*?```|`[^`\n]*`)/)
    .map((part, i) => {
      if (i % 2) return part;
      let text = part;
      for (const [pattern, display] of FORMULAS) {
        text = text.replace(pattern, (_, tex) => {
          found.push(renderMath(tex.trim(), display));
          return `\u0001${found.length - 1}\u0001`;
        });
      }
      return text;
    })
    .join("");
}

export function renderMarkdown(raw) {
  const formulas = [];
  const source = takeFormulas(String(raw).replace(/[\u0000\u0001]/g, ""), formulas);
  return putFormulas(renderBlocks(source), formulas);
}

function putFormulas(html, formulas) {
  return html.replace(/\u0001(\d+)\u0001/g, (_, i) => formulas[Number(i)]);
}

function renderBlocks(source) {
  const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let paragraph = [];
  let list = null;
  let quote = [];
  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    paragraph = [];
  };
  const flushQuote = () => {
    if (quote.length) out.push(`<blockquote>${quote.map(inline).join("<br>")}</blockquote>`);
    quote = [];
  };
  const flushAll = () => {
    flushParagraph();
    flushQuote();
    flushList(out, list);
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      flushAll();
      const body = [];
      for (i += 1; i < lines.length && !/^\s*```\s*$/.test(lines[i]); i++) body.push(lines[i]);
      const lang = fence[1] ? ` data-lang="${escapeHtml(fence[1])}"` : "";
      out.push(`<pre class="md-pre"${lang}><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }
    if (!line.trim()) {
      flushAll();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushAll();
      out.push(`<h${heading[1].length + 2} class="md-h">${inline(heading[2])}</h${heading[1].length + 2}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      flushQuote();
      const tag = bullet ? "ul" : "ol";
      if (list && list.tag !== tag) {
        flushList(out, list);
        list = null;
      }
      list ??= { tag, items: [] };
      list.items.push((bullet ?? numbered)[1]);
      continue;
    }
    const quoted = line.match(/^>\s?(.*)$/);
    if (quoted) {
      flushParagraph();
      flushList(out, list);
      list = null;
      quote.push(quoted[1]);
      continue;
    }
    flushQuote();
    flushList(out, list);
    list = null;
    paragraph.push(line);
  }
  flushAll();
  return out.join("");
}
