// Syntax colours for code the agent writes and for fenced code in answers: a small line tokenizer
// (keywords, strings, numbers, comments, calls, types), coloured by theme classes. It is not a parser:
// a block comment or a string that spans lines is coloured on its first line only, which is enough
// to read code as it streams in.

const KEYWORDS = {
  py: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case self",
  js: "async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null of return static super switch this throw true try typeof undefined var void while with yield interface type enum implements readonly as",
  rs: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while Some None Ok Err",
  go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false",
  sh: "if then else elif fi for while do done case esac in function return local export echo exit set unset readonly shift source",
  sql: "select from where and or not insert into values update set delete create table alter drop join left right inner outer on group by order having limit offset as distinct union all null is in like between case when then else end primary key foreign references index",
  c: "auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected namespace template typename new delete true false nullptr this virtual override bool",
  java: "abstract boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new null package private protected public return short static super switch synchronized this throw throws try void volatile while true false var record",
  css: "important media supports keyframes from to and not only",
};

const FAMILY = {
  py: "py", js: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js", jsx: "js", rs: "rs", go: "go",
  sh: "sh", bash: "sh", zsh: "sh", sql: "sql", c: "c", h: "c", cpp: "c", hpp: "c", cc: "c", cs: "c",
  java: "java", kt: "java", swift: "java", css: "css", scss: "css",
  python: "py", javascript: "js", typescript: "js", rust: "rs", shell: "sh", golang: "go",
};

const LINE_COMMENT = { py: "#", sh: "#", sql: "--", js: "//", rs: "//", go: "//", c: "//", java: "//", css: null };

const sets = new Map();
function keywordsOf(family) {
  if (!sets.has(family)) sets.set(family, new Set((KEYWORDS[family] ?? "").split(" ")));
  return sets.get(family);
}

/** The highlighter's family for a file name or a fence's language, or "" when it has none. */
export function familyOf(nameOrLanguage) {
  const text = String(nameOrLanguage ?? "").toLowerCase();
  const extension = text.includes(".") ? text.split("/").pop().split(".").pop() : text;
  return FAMILY[extension] ?? "";
}

const escapeHtml = (text) => text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const span = (kind, text) => `<span class="tk-${kind}">${escapeHtml(text)}</span>`;

/** One line of code as HTML with coloured tokens; plain (escaped) when the family is unknown. */
export function highlightLine(line, family) {
  if (!family) return escapeHtml(line);
  const keywords = keywordsOf(family);
  const comment = LINE_COMMENT[family];
  let out = "";
  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    if ((comment && rest.startsWith(comment)) || rest.startsWith("/*")) {
      const end = rest.startsWith("/*") ? rest.indexOf("*/", 2) : -1;
      const piece = end >= 0 ? rest.slice(0, end + 2) : rest;
      out += span("com", piece);
      i += piece.length;
      continue;
    }
    const quote = rest[0];
    if (quote === '"' || quote === "'" || quote === "`") {
      let j = 1;
      while (j < rest.length && rest[j] !== quote) j += rest[j] === "\\" ? 2 : 1;
      const piece = rest.slice(0, j + 1);
      out += span("str", piece);
      i += piece.length;
      continue;
    }
    const number = /^(0x[\da-f_]+|\d[\d_]*(\.\d+)?(e[+-]?\d+)?)/i.exec(rest);
    if (number && !/[\w$]/.test(line[i - 1] ?? "")) {
      out += span("num", number[0]);
      i += number[0].length;
      continue;
    }
    const word = /^[A-Za-z_$][\w$]*/.exec(rest);
    if (word) {
      const name = word[0];
      const after = line.slice(i + name.length);
      if (keywords.has(family === "sql" ? name.toLowerCase() : name)) out += span("kw", name);
      else if (/^\s*\(/.test(after)) out += span("fn", name);
      else if (/^[A-Z][a-z]/.test(name)) out += span("type", name);
      else out += escapeHtml(name);
      i += name.length;
      continue;
    }
    out += escapeHtml(rest[0]);
    i += 1;
  }
  return out;
}
