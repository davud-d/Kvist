/**
 * template.mjs — one page, rendered twice.
 *
 * Both locales are fully rendered here at build time, including the demo's
 * final frame, so the first HTML response is the whole page. Nothing below
 * needs JavaScript to be readable.
 */

const SITE = "https://kvist.site";

const esc = (value) =>
  String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

/** JSON going inside a <script> element, where esc() would corrupt it.
    JSON.stringify escapes neither "<" nor "/", so a string holding
    "</script>" would close the element it is sitting in. < is the same
    string to a JSON parser and inert to the HTML one. */
const escJson = (value) => JSON.stringify(value).replace(/</g, "\\u003c");

/** The theme decision, inline and before first paint, so nothing flashes. */
const BOOT = `var d=document.documentElement;d.classList.remove('no-js');d.classList.add('js');try{var t=localStorage.getItem('kvist.theme');if(t){d.dataset.theme=t}}catch(e){}`;

/** True for an --ansi-0..15 index. The compiler only ever writes integers here,
    but this is the one place a cast reaches markup instead of a DOM property,
    so the colour is checked rather than trusted. */
const isSlot = (value) => Number.isInteger(value) && value >= 0 && value <= 15;

/** The recording's final frame, as static markup for the no-JavaScript case. */
function finalFrame(cast) {
  return cast.final
    .map((id) => {
      const spans = cast.rows[id]
        .map(([text, fg, bg, bold]) => {
          const style = [
            isSlot(fg) ? `color:var(--ansi-${fg})` : "",
            isSlot(bg) ? `background:var(--ansi-${bg})` : "",
          ]
            .filter(Boolean)
            .join(";");
          const tag = bold ? "b" : "span";
          const attr = style ? ` style="${style}"` : "";
          return `<${tag}${attr}>${esc(text)}</${tag}>`;
        })
        .join("");
      return `<span>${spans || " "}</span>`;
    })
    .join("");
}

/** The front page's recorded run: one clip per interface, the first shown.
    Both <video> elements are in the markup so nothing needs JavaScript; main.js
    adds the switch and hides the one not chosen. Nothing is fetched until play. */
function demoVideos(t) {
  const clips = [
    { id: "classic", label: t.demo.tabClassic },
    { id: "claude", label: t.demo.tabClaude },
  ];
  return `<div class="clips" data-clips>
        <div class="clip-tabs" role="group" aria-label="${esc(t.demo.tabsLabel)}">
          ${clips
            .map(
              (clip, index) =>
                `<button class="chip" type="button" data-clip-tab="${clip.id}" aria-pressed="${index === 0}">${esc(clip.label)}</button>`,
            )
            .join("\n          ")}
        </div>
        ${clips
          .map(
            (clip) => `<video class="clip" data-clip="${clip.id}" controls playsinline preload="none"
               poster="/assets/demo/video/${clip.id}.jpg" aria-label="${esc(clip.label)}">
          <source src="/assets/demo/video/${clip.id}.mp4" type="video/mp4">
        </video>`,
          )
          .join("\n        ")}
      </div>`;
}

/** Colours a shell command at build time, so it needs no script and the copy
    button still carries the plain text. Only the shapes these commands have are
    recognised: the program at the start of each pipe segment, flags, URLs and
    the pipe itself. Everything else stays plain. */
function highlight(cmd) {
  let startOfSegment = true;
  return cmd
    .split(/(\s+)/)
    .map((part) => {
      if (!part.trim()) return esc(part);
      let kind = "";
      if (part === "|") {
        kind = "pipe";
        startOfSegment = true;
        return `<span class="tk-${kind}">${esc(part)}</span>`;
      }
      if (startOfSegment) kind = "cmd";
      else if (/^https?:\/\//.test(part)) kind = "url";
      else if (part.startsWith("-")) kind = "flag";
      startOfSegment = false;
      return kind ? `<span class="tk-${kind}">${esc(part)}</span>` : esc(part);
    })
    .join("");
}

/** A command the reader will paste. The text sits in a <code> that selects whole
    on a click, so copying works with no script and no clipboard permission; the
    button is the convenience on top, added to the page by main.js. */
function cmdBlock(t, { label, cmd }) {
  return `<div class="cmd">
          ${label ? `<span class="cmd-label">${esc(label)}</span>` : ""}
          <div class="cmd-row">
            <code class="cmd-text" tabindex="0">${highlight(cmd)}</code>
            <button class="chip" type="button" data-copy="${esc(cmd)}"
                    data-done="${esc(t.installPage.copied)}"
                    aria-label="${esc(t.installPage.copyLabel)}: ${esc(cmd)}" hidden>${esc(t.installPage.copy)}</button>
          </div>
        </div>`;
}

/** Where to look in each screenshot: its size in pixels and the outlined boxes as
    [x, y, width, height, side?] in that size, in the order the legend lists them.
    `side` "r" puts the number on the right edge, for boxes at the image's left
    edge where the default (left of the box) would be cut off. Geometry is not
    copy, so it lives here and the two locales only translate the legend. */
const FULL = [1500, 900];
const SHOTS = {
  run: { size: [1596, 200], boxes: [[44, 50, 608, 26], [44, 76, 1508, 50]] },
  components: { size: FULL, boxes: [[430, 318, 640, 24], [430, 366, 640, 24], [430, 538, 640, 120], [430, 678, 92, 24]] },
  progress: { size: FULL, boxes: [[430, 415, 260, 72], [430, 508, 420, 52]] },
  welcome: { size: FULL, boxes: [[430, 271, 130, 24], [430, 583, 440, 24], [430, 630, 130, 26]] },
  provider: { size: FULL, boxes: [[430, 414, 640, 24], [430, 438, 640, 24], [430, 582, 640, 24]] },
  key: { size: FULL, boxes: [[430, 391, 570, 24], [430, 438, 640, 26], [430, 486, 280, 26], [430, 534, 100, 26]] },
  start: { size: FULL, boxes: [[450, 390, 600, 72], [450, 488, 600, 22], [1148, 846, 325, 26], [28, 846, 115, 26]] },
  task: { size: FULL, boxes: [[450, 390, 600, 72]] },
  commands: { size: FULL, boxes: [[450, 127, 600, 238]] },
};

/** Empty ${} slots leave indented blank lines behind. */
const tidy = (html) => html.replace(/[ \t]+$/gm, "");

const pct = (value, whole) => `${((value / whole) * 100).toFixed(3)}%`;

/** A screenshot with numbered outlines and the legend that explains them. The
    outlines are positioned in percentages of the image, so they stay on their
    targets at any width; the numbers carry the meaning, the colour only helps. */
function shot({ shot: item }) {
  const { size, boxes } = SHOTS[item.id];
  if (boxes.length !== item.marks.length) {
    throw new Error(`shot "${item.id}": ${boxes.length} boxes, ${item.marks.length} legend lines`);
  }
  const [w, h] = size;
  return `<figure class="shot">
          <div class="shot-frame" style="aspect-ratio:${w}/${h}">
            <img src="/assets/install/${esc(item.id)}.webp" width="${w}" height="${h}" loading="lazy" decoding="async" alt="${esc(item.alt)}">
            ${boxes
              .map(
                ([x, y, bw, bh, side], index) =>
                  `<span class="shot-mark${side === "r" ? " shot-mark-r" : ""}" aria-hidden="true" style="left:${pct(x, w)};top:${pct(y, h)};width:${pct(bw, w)};height:${pct(bh, h)}"><i>${index + 1}</i></span>`,
              )
              .join("\n            ")}
          </div>
          <ol class="legend">
            ${item.marks.map((text) => `<li>${esc(text)}</li>`).join("\n            ")}
          </ol>
        </figure>`;
}

const cards = (items) =>
  items
    .map(
      (item) => `<li class="card">
            <span class="term">${esc(item.term)}</span>
            <p>${esc(item.text)}</p>
          </li>`,
    )
    .join("\n          ");

function head({ t, css, meta, path, otherPath }) {
  const url = SITE + path;
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Kvist",
    applicationCategory: "DeveloperApplication",
    operatingSystem: "Linux, Windows",
    description: meta.description,
    url,
    programmingLanguage: "Python",
    license: "https://opensource.org/licenses/MIT",
    inLanguage: t.lang,
    offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
  };
  const enPath = t.lang === "en" ? path : otherPath;
  const ruPath = t.lang === "ru" ? path : otherPath;
  return `<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(meta.title)}</title>
<meta name="description" content="${esc(meta.description)}">
<link rel="canonical" href="${esc(url)}">
<link rel="alternate" hreflang="en" href="${SITE}${esc(enPath)}">
<link rel="alternate" hreflang="ru" href="${SITE}${esc(ruPath)}">
<link rel="alternate" hreflang="x-default" href="${SITE}${esc(enPath)}">
<meta property="og:type" content="website">
<meta property="og:url" content="${esc(url)}">
<meta property="og:title" content="${esc(meta.title)}">
<meta property="og:description" content="${esc(meta.description)}">
<meta property="og:image" content="${SITE}/assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="${esc(t.meta.ogAlt)}">
<meta property="og:locale" content="${t.lang === "ru" ? "ru_RU" : "en_US"}">
<meta property="og:locale:alternate" content="${t.lang === "ru" ? "en_US" : "ru_RU"}">
<meta name="twitter:card" content="summary_large_image">
<meta name="color-scheme" content="dark light">
<link rel="icon" type="image/png" sizes="64x64" href="/assets/favicon.png">
<link rel="preload" href="/assets/fonts/kvist-mono-latin-400.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/assets/fonts/kvist-mono-latin-700.woff2" as="font" type="font/woff2" crossorigin>${
  t.lang === "ru"
    ? `\n<link rel="preload" href="/assets/fonts/kvist-mono-cyrillic-400.woff2" as="font" type="font/woff2" crossorigin>` +
      `\n<link rel="preload" href="/assets/fonts/kvist-mono-cyrillic-700.woff2" as="font" type="font/woff2" crossorigin>`
    : ""
}
<script>${BOOT}</script>
<style>${css}</style>
<script type="application/ld+json">${escJson(jsonLd)}</script>`;
}

/** The header is shared; only the rail contents differ between the pages. */
function header({ t, home, rail, label = t.meta.title }) {
  return `<header class="top">
  <div class="wrap top-row">
    <a class="mark" href="${esc(home)}"><span class="dot" aria-hidden="true"></span>Kvist</a>
    <nav class="rail" aria-label="${esc(label)}">
      ${rail}
    </nav>
    <div class="tools">
      <button class="chip" type="button" data-theme-toggle
              data-dark="${esc(t.nav.themeDark)}" data-light="${esc(t.nav.themeLight)}"
              aria-label="${esc(t.nav.theme)}">${esc(t.nav.themeLight)}</button>
      <a class="chip" href="${esc(t.other.dir)}" lang="${esc(t.other.lang)}"
         hreflang="${esc(t.other.lang)}" aria-label="${esc(t.other.name)}">${esc(t.other.label)}</a>
    </div>
  </div>
</header>`;
}

function langbar(t, otherPath) {
  return `<aside class="langbar" data-langbar="${esc(t.other.lang)}" hidden
       aria-label="${esc(t.other.name)}">
  <div class="wrap langbar-row">
    <p>${esc(t.langbar.text)}</p>
    <a class="chip" href="${esc(otherPath)}">${esc(t.langbar.go)}</a>
    <button class="chip" type="button" data-langbar-close>${esc(t.langbar.dismiss)}</button>
  </div>
</aside>`;
}

export function render({ t, css, buildId }) {
  const rail = ["demo", "loop", "boundary", "recovery", "control", "anymodel", "install"]
    .map((key) => `<a href="#${key}">${esc(t.nav[key])}</a>`)
    .join("\n      ") +
    `\n      <a class="rail-cta" href="${esc(t.dir)}install/">${esc(t.nav.howto)}</a>`;

  return tidy(`<!DOCTYPE html>
<html lang="${esc(t.lang)}" class="no-js">
<head>
${head({ t, css, meta: t.meta, path: t.dir, otherPath: t.other.dir })}
</head>
<body>
<a class="skip" href="#demo">${esc(t.nav.skip)}</a>
<div class="texture" aria-hidden="true"></div>
<div class="cursor-glow" data-cursor-glow aria-hidden="true"></div>

${header({ t, home: t.dir, rail })}

<main>
  <section class="hero">
    <canvas class="hero-canvas" data-hero-canvas aria-hidden="true"></canvas>
    <div class="hero-veil" aria-hidden="true"></div>
    <div class="wrap hero-body">
      <p><span class="invert">${esc(t.hero.status)}</span></p>
      <h1>${esc(t.hero.h1)}</h1>
      <p class="lede">${esc(t.hero.lede)}</p>

      <p class="hero-cta"><a class="btn" href="${esc(t.dir)}install/">${esc(t.hero.installLink)}</a></p>

      <div class="hero-links">
        <a class="linkout" href="#demo">${esc(t.hero.cta)}</a>
        <a class="linkout" href="https://github.com/davud-d/Kvist">${esc(t.hero.repo)}</a>
      </div>
      <p class="hero-note">${esc(t.hero.note)}</p>
    </div>
  </section>

  <section class="section" id="demo">
    <div class="wrap">
      <p class="kicker">${esc(t.demo.kicker)}</p>
      <h2 class="h2">${esc(t.demo.h2)}</h2>
      <p class="lede">${esc(t.demo.lede)}</p>

      ${demoVideos(t)}
    </div>
  </section>

  ${["loop", "boundary", "recovery", "control", "anymodel"]
    .map(
      (key) => `<section class="section" id="${key}">
    <div class="wrap">
      <p class="kicker">${esc(t[key].kicker)}</p>
      <h2 class="h2">${esc(t[key].h2)}</h2>
      <p class="lede">${esc(t[key].lede)}</p>
      <ul class="grid reveal">
          ${cards(t[key].items)}
      </ul>${
        t[key].honest
          ? `\n      <p class="note">${esc(t[key].honest)}</p>`
          : ""
      }
    </div>
  </section>`,
    )
    .join("\n\n  ")}

  <section class="section" id="install">
    <div class="wrap">
      <p class="kicker">${esc(t.install.kicker)}</p>
      <h2 class="h2">${esc(t.install.h2)}</h2>
      <p class="lede">${esc(t.install.lede)}</p>

      <ol class="steps reveal">
        ${t.install.steps
          .map(
            (step) => `<li class="step">
          <span class="n">${esc(step.n)}</span>
          <div><span class="term">${esc(step.term)}</span>${
            step.cmd ? cmdBlock(t, { cmd: step.cmd }) : `<p>${esc(step.text)}</p>`
          }</div>
        </li>`,
          )
          .join("\n        ")}
      </ol>
      <p class="hero-links" style="margin-top:var(--s-5)"><a class="linkout" href="${esc(t.dir)}install/">${esc(t.install.more)}</a></p>

      <h3 class="term" style="margin-top:var(--s-6)">${esc(t.install.reqTitle)}</h3>
      <ul class="req">
        ${t.install.req.map((item) => `<li>${esc(item)}</li>`).join("\n        ")}
      </ul>
      <p class="note">${esc(t.install.alt)}</p>
    </div>
  </section>
</main>

<footer class="foot">
  <div class="wrap">
    <p>${esc(t.footer.release)}</p>
    <div class="foot-row" style="margin-top:var(--s-4)">
      <span>${esc(t.footer.license)}</span>
      <a href="https://github.com/davud-d/Kvist">${esc(t.footer.repo)}</a>
      <span>${esc(t.footer.builtWith)}</span>
    </div>
  </div>
</footer>

${langbar(t, t.other.dir)}

<script type="module" src="/assets/app.js?v=${buildId}"></script>
</body>
</html>
`);
}

export { SITE, BOOT };

/**
 * The install page: one section per step, each a short text, the commands to
 * paste and a screenshot of the real screen. Same header and footer as the front
 * page, and complete without JavaScript (the copy buttons are the only addition).
 */
export function renderInstall({ t, css, buildId }) {
  const page = t.installPage;
  const path = `${t.dir}install/`;
  const otherPath = `${t.other.dir}install/`;
  const rail = page.steps
    .map((step) => `<a href="#${esc(step.id)}">${esc(step.n)}</a>`)
    .join("\n      ");

  return tidy(`<!DOCTYPE html>
<html lang="${esc(t.lang)}" class="no-js install">
<head>
${head({ t, css, meta: page.meta, path, otherPath })}
</head>
<body>
<a class="skip" href="#${esc(page.steps[0].id)}">${esc(page.skip)}</a>
<div class="texture" aria-hidden="true"></div>
<div class="cursor-glow" data-cursor-glow aria-hidden="true"></div>

${header({ t, home: t.dir, rail, label: page.meta.title })}

<main>
  <section class="section section-lead">
    <div class="wrap">
      <p class="kicker">${esc(page.kicker)}</p>
      <h1 class="h1-page">${esc(page.h1)}</h1>
      <p class="lede">${esc(page.lede)}</p>
      <h2 class="term" style="margin-top:var(--s-5)">${esc(page.needTitle)}</h2>
      <ul class="req" style="margin-top:var(--s-3)">
        ${page.need.map((item) => `<li>${esc(item)}</li>`).join("\n        ")}
      </ul>
      <p class="note">
        <span class="term">${esc(page.caveat.term)}</span>
        ${esc(page.caveat.text)}
      </p>
    </div>
  </section>

  ${page.steps
    .map(
      (step) => `<section class="section istep" id="${esc(step.id)}" aria-labelledby="${esc(step.id)}-h">
    <div class="wrap">
      <div class="guide-head">
        <span class="n"><span class="visually-hidden">${esc(page.stepWord)} </span>${esc(step.n)}</span>
        <div>
          <h2 class="h2" id="${esc(step.id)}-h">${esc(step.term)}</h2>
          <p class="lede">${esc(step.text)}</p>
        </div>
      </div>
      ${(step.cmds ?? []).map((cmd) => cmdBlock(t, cmd)).join("\n      ")}${
        step.note ? `\n      <p class="note">${esc(step.note)}</p>` : ""
      }
      ${step.shots.map((item) => shot({ shot: item })).join("\n      ")}
    </div>
  </section>`,
    )
    .join("\n\n  ")}

  <section class="section">
    <div class="wrap">
      <p class="note" style="margin-top:0">${esc(page.alt)}</p>
    </div>
  </section>
</main>

<footer class="foot">
  <div class="wrap">
    <div class="foot-row">
      <a class="linkout" href="${esc(t.dir)}">${esc(page.back)}</a>
      <a href="https://github.com/davud-d/Kvist">${esc(t.footer.repo)}</a>
      <span>${esc(t.footer.license)}</span>
    </div>
  </div>
</footer>

${langbar(t, otherPath)}

<script type="module" src="/assets/app.js?v=${buildId}"></script>
</body>
</html>
`);
}
