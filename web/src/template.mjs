/**
 * template.mjs — one page, rendered twice.
 *
 * Both locales are fully rendered here at build time, including the demo's
 * final frame, so the first HTML response is the whole page. Nothing below
 * needs JavaScript to be readable.
 */

const SITE = "https://kvist.dev";
const REPO = "https://github.com/davud-d/Kvist";

const esc = (value) =>
  String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

/** The theme decision, inline and before first paint, so nothing flashes. */
const BOOT = `var d=document.documentElement;d.classList.remove('no-js');d.classList.add('js');try{var t=localStorage.getItem('kvist.theme');if(t){d.dataset.theme=t}}catch(e){}`;

/** The recording's final frame, as static markup for the no-JavaScript case. */
function finalFrame(cast) {
  return cast.final
    .map((id) => {
      const spans = cast.rows[id]
        .map(([text, fg, bg, bold]) => {
          const style = [
            fg !== -1 ? `color:var(--ansi-${fg})` : "",
            bg !== -2 ? `background:var(--ansi-${bg})` : "",
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

/** One replay: server-rendered final frame plus the controls that drive it. */
function replay({ t, cast, source, label, autoplay }) {
  return `<div class="replay" data-replay data-cast="${source}"
           data-play="${esc(t.demo.play)}" data-pause="${esc(t.demo.pause)}"
           ${autoplay ? "" : 'data-autoplay="false"'}>
        <div class="replay-wrap">
          <pre class="replay-screen" data-screen tabindex="0"
               style="--rows:${cast.h}"
               aria-label="${esc(label)}">${finalFrame(cast)}</pre>
        </div>
        <div class="replay-bar">
          <button class="chip" type="button" data-play aria-pressed="false">${esc(t.demo.play)}</button>
          <button class="chip" type="button" data-restart>${esc(t.demo.restart)}</button>
          <button class="chip" type="button" data-speed aria-label="${esc(t.demo.speed)}">1&#215;</button>
          <input class="scrub" type="range" min="0" max="${cast.duration}" value="0" step="100"
                 data-scrub disabled aria-label="${esc(t.demo.progress)}">
          <span class="replay-time" data-time>0:00 / 0:00</span>
        </div>
      </div>`;
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
    softwareVersion: "beta",
    programmingLanguage: "Python",
    codeRepository: REPO,
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
<link rel="canonical" href="${url}">
<link rel="alternate" hreflang="en" href="${SITE}${enPath}">
<link rel="alternate" hreflang="ru" href="${SITE}${ruPath}">
<link rel="alternate" hreflang="x-default" href="${SITE}${enPath}">
<meta property="og:type" content="website">
<meta property="og:url" content="${url}">
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
<script type="application/ld+json">${JSON.stringify(jsonLd)}</script>`;
}

/** The header is shared; only the rail contents differ between the pages. */
function header({ t, home, rail }) {
  return `<header class="top">
  <div class="wrap top-row">
    <a class="mark" href="${home}"><span class="dot" aria-hidden="true"></span>Kvist</a>
    <nav class="rail" aria-label="${esc(t.meta.title)}">
      ${rail}
    </nav>
    <div class="tools">
      <button class="chip" type="button" data-theme-toggle
              data-dark="${esc(t.nav.themeDark)}" data-light="${esc(t.nav.themeLight)}"
              aria-label="${esc(t.nav.theme)}">${esc(t.nav.themeLight)}</button>
      <a class="chip" href="${t.other.dir}" lang="${t.other.lang}"
         hreflang="${t.other.lang}" aria-label="${esc(t.other.name)}">${esc(t.other.label)}</a>
    </div>
  </div>
</header>`;
}

function langbar(t, otherPath) {
  return `<aside class="langbar" data-langbar="${t.other.lang}" hidden
       aria-label="${esc(t.other.name)}">
  <div class="wrap langbar-row">
    <p>${esc(t.langbar.text)}</p>
    <a class="chip" href="${otherPath}">${esc(t.langbar.go)}</a>
    <button class="chip" type="button" data-langbar-close>${esc(t.langbar.dismiss)}</button>
  </div>
</aside>`;
}

export function render({ t, css, cast, buildId }) {
  const rail = ["demo", "loop", "boundary", "recovery", "control", "install"]
    .map((key) => `<a href="#${key}">${esc(t.nav[key])}</a>`)
    .join("\n      ") +
    `\n      <a href="${t.dir}guides/">${esc(t.guides.nav)}</a>`;

  return `<!DOCTYPE html>
<html lang="${t.lang}" class="no-js">
<head>
${head({ t, css, meta: t.meta, path: t.dir, otherPath: t.other.dir })}
</head>
<body>
<a class="skip" href="#install-command">${esc(t.nav.skip)}</a>
<div class="texture" aria-hidden="true"></div>

${header({ t, home: t.dir, rail })}

<main>
  <section class="hero">
    <canvas class="hero-canvas" data-hero-canvas aria-hidden="true"></canvas>
    <div class="hero-veil" aria-hidden="true"></div>
    <div class="wrap hero-body">
      <p><span class="invert">${esc(t.hero.status)}</span></p>
      <h1>${esc(t.hero.h1)}</h1>
      <p class="lede">${esc(t.hero.lede)}</p>

      <div class="cmd">
        <div class="cmd-head">
          <span>${esc(t.hero.commandLabel)}</span>
          <button class="copy" type="button" hidden data-copy="install-command"
                  data-idle="${esc(t.hero.copy)}" data-done="${esc(t.hero.copied)}"
                  data-copied="false">${esc(t.hero.copy)}</button>
        </div>
        <div class="cmd-body">
          <code id="install-command" tabindex="0">${esc(t.hero.command)}</code>
        </div>
        <p class="cmd-hint">${esc(t.hero.copyHint)}</p>
      </div>

      <div class="hero-links">
        <a class="linkout" href="${REPO}" rel="noopener">${esc(t.hero.repo)}</a>
        <span class="hero-note">${esc(t.hero.note)}</span>
      </div>
    </div>
  </section>

  <section class="section" id="demo">
    <div class="wrap">
      <p class="kicker">${esc(t.demo.kicker)}</p>
      <h2 class="h2">${esc(t.demo.h2)}</h2>
      <p class="lede">${esc(t.demo.lede)}</p>

      ${replay({
        t,
        cast,
        source: `/assets/demo/run-${t.lang}.json`,
        label: t.demo.h2,
        autoplay: true,
      })}
      <p class="sr">${esc(t.demo.srHint)}</p>

      <div class="task">
        <span class="term">${esc(t.demo.task)}</span>
        ${esc(t.demo.taskText)}
      </div>
      <p class="hero-note">${esc(t.demo.reRecord)}</p>
    </div>
  </section>

  ${["loop", "boundary", "recovery", "control"]
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
          <div><span class="term">${esc(step.term)}</span><p>${esc(step.text)}</p></div>
        </li>`,
          )
          .join("\n        ")}
      </ol>

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
    <p>${esc(t.footer.beta)}</p>
    <div class="foot-row" style="margin-top:var(--s-4)">
      <span>${esc(t.footer.license)}</span>
      <a href="${REPO}" rel="noopener">${esc(t.footer.repo)}</a>
      <span>${esc(t.footer.builtWith)}</span>
    </div>
  </div>
</footer>

${langbar(t, t.other.dir)}

<script type="module" src="/assets/app.js?v=${buildId}"></script>
</body>
</html>
`;
}

export { SITE, REPO };

/**
 * The guides page: one section per recording, each a short piece of prose and
 * the terminal it describes. Same header, same replay component, same rules —
 * the final frame is server-rendered, so the page is complete without
 * JavaScript here too.
 */
export function renderGuides({ t, css, casts, buildId }) {
  const path = `${t.dir}guides/`;
  const otherPath = `${t.other.dir}guides/`;
  /* Twelve entries do not fit a rail, so it lists the four groups. */
  const rail = t.guides.groups
    .map((group) => `<a href="#${group.id}">${esc(group.title)}</a>`)
    .join("\n      ");

  return `<!DOCTYPE html>
<html lang="${t.lang}" class="no-js guides">
<head>
${head({ t, css, meta: t.guides.meta, path, otherPath })}
</head>
<body>
<a class="skip" href="#${t.guides.items[0].id}">${esc(t.guides.skip)}</a>
<div class="texture" aria-hidden="true"></div>

${header({ t, home: t.dir, rail })}

<main>
  <section class="section section-lead">
    <div class="wrap">
      <p class="kicker">${esc(t.guides.kicker)}</p>
      <h1 class="h1-page">${esc(t.guides.h1)}</h1>
      <p class="lede">${esc(t.guides.lede)}</p>
      <p class="note">
        <span class="term">${esc(t.guides.caveat.term)}</span>
        ${esc(t.guides.caveat.text)}
      </p>
    </div>
  </section>

  ${t.guides.groups
    .map((group) => {
      const items = t.guides.items.filter((item) => item.group === group.id);
      return `<section class="section" id="${group.id}">
    <div class="wrap">
      <p class="kicker">${esc(group.title)}</p>
    </div>
    ${items
      .map(
        (item) => `<div class="guide" id="${item.id}">
      <div class="wrap">
        <div class="guide-head">
          <span class="n">${esc(item.n)}</span>
          <div>
            <h2 class="h2">${esc(item.term)}</h2>
            <p class="lede">${esc(item.text)}</p>
          </div>
        </div>
        ${replay({
          t,
          cast: casts[item.id],
          source: `/assets/demo/guide-${item.id}.json`,
          label: item.term,
          autoplay: false,
        })}
      </div>
    </div>`,
      )
      .join("\n    ")}
  </section>`;
    })
    .join("\n\n  ")}
</main>

<footer class="foot">
  <div class="wrap">
    <div class="foot-row">
      <a class="linkout" href="${t.dir}">${esc(t.guides.back)}</a>
      <a href="${REPO}" rel="noopener">${esc(t.footer.repo)}</a>
      <span>${esc(t.footer.license)}</span>
    </div>
  </div>
</footer>

${langbar(t, otherPath)}

<script type="module" src="/assets/app.js?v=${buildId}"></script>
</body>
</html>
`;
}
