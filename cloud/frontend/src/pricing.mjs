// The pricing page, rendered at build time per locale. No script: the button is a link, so the
// page works the moment its HTML arrives, also on a slow phone with JavaScript blocked.

const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function duration(minutes, t) {
  if (!minutes) return t.pricing.none;
  if (minutes % 60 === 0) return t.units.h.replace("{h}", minutes / 60);
  return t.units.min.replace("{m}", minutes);
}

// About 82.2 RUB per USD (the Central Bank of Russia, 19 Nov 2026), rounded to ten. Only what the page shows:
// the amount charged is the server's PLATEGA_PRICE_*, shown again on the payment screen.
const RUB = { start: 1230, pro: 3210, max: 8140 };
const spaced = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");

function price(t, plan) {
  const p = t.pricing;
  return `<span class="usd">${esc(p.perMonth.replace("{p}", plan.price_usd))}</span><span class="rub">${esc(p.perMonthRub.replace("{p}", spaced(RUB[plan.id])))}</span>`;
}

function table(t, plans) {
  const p = t.pricing;
  const rows = plans.map((plan) => `<tr>
<th scope="row">${esc(p.planNames[plan.id])}</th>
<td${plan.id === "trial" ? ' class="now"' : ""}>${plan.id === "trial" ? esc(p.trialPerWeek) : price(t, plan)}</td>
<td>${plan.hours}</td>
<td>${plan.vcpu} vCPU (shared) · ${plan.memory_gb} GB</td>
<td>${plan.disk_gb} GB</td>
<td>${plan.max_parallel_tasks}</td>
<td>${esc(duration(plan.command_minutes, t))}</td>
<td>${esc(duration(plan.background_minutes, t))}</td>
</tr>`).join("\n");
  return `<div class="table-scroll"><table class="plans">
<thead><tr><th scope="col">${esc(p.colPlan)}</th><th scope="col">${esc(p.colPrice)}</th><th scope="col">${esc(p.colHours)}</th><th scope="col">${esc(p.colMachine)}</th><th scope="col">${esc(p.colDisk)}</th><th scope="col">${esc(p.colParallel)}</th><th scope="col">${esc(p.colCommand)}</th><th scope="col">${esc(p.colBackground)}</th></tr></thead>
<tbody>
${rows}
</tbody></table></div>`;
}

function paidCards(t, plans) {
  const p = t.pricing;
  const cards = plans.filter((plan) => plan.id !== "trial").map((plan) => `<div class="card">
<p class="spread"><span class="h3">${esc(p.planNames[plan.id])}</span><span class="price">${price(t, plan)}</span></p>
<ul class="facts"><li>${esc(p.paidHours.replace("{h}", plan.hours))}</li><li>${plan.vcpu} vCPU (shared) · ${plan.memory_gb} GB RAM · ${plan.disk_gb} GB</li></ul>
<p><a class="btn ghost wide" href="/auth/github/login?next=/start">${esc(p.pay)}</a></p>
</div>`).join("\n");
  return `<h2 class="section-title" id="paid-title">${esc(p.paidTitle)}</h2>
<div class="stack">
${cards}
</div>
<p class="small rub-note">${esc(p.rubNote)}</p>`;
}

export function renderPricing(t, catalog, styles) {
  const p = t.pricing;
  const other = t.lang === "ru" ? "en" : "ru";
  return `<!doctype html>
<html lang="${t.lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(t.meta.title)}</title>
<meta name="description" content="${esc(t.meta.description)}">
<link rel="icon" href="data:,">
<style>${styles}</style>
</head>
<body>
<a class="skip" href="#main">${esc(t.common.skip)}</a>
<input class="cur-box" type="checkbox" id="cur" aria-label="${esc(p.curSwitch)}">
<header class="top"><div class="wrap top-row">
<a class="mark" href="/"><span class="dot" aria-hidden="true"></span>${esc(t.common.brand)}<span class="sub">${esc(t.common.cloud)}</span></a>
<nav class="nav" aria-label="language"><label class="chip cur-switch" for="cur" title="${esc(p.curSwitch)}"><span class="usd">$</span><span class="rub">₽</span> ⇄</label> <a class="chip" href="/?lang=${other}" hreflang="${other}" lang="${other}" aria-label="${esc(t.common.otherLangName)}">${esc(t.common.otherLang)}</a></nav>
</div></header>
<main class="wrap" id="main">
<section class="hero">
<p class="kicker">${esc(p.kicker)}</p>
<h1 class="h1">${esc(p.title)}</h1>
<p class="lede">${esc(p.lede)}</p>
</section>
<section class="offer card chosen" aria-labelledby="trial-title">
<p class="spread"><span class="h3" id="trial-title">${esc(p.trialName)}</span><span class="price">${esc(p.trialPrice)}</span></p>
<ul class="facts">${p.facts.map((fact) => `<li>${esc(fact)}</li>`).join("")}</ul>
<p><a class="btn wide" href="/auth/github/login?next=/start">${esc(p.cta)}</a></p>
<p class="terms">${esc(p.terms)}</p>
<p class="small">${esc(p.github)}</p>
</section>
<section class="stack" style="margin-top:32px">
${paidCards(t, catalog.plans)}
<details class="compare"><summary>${esc(p.compare)}</summary>
${table(t, catalog.plans)}
</details>
<h2 class="section-title" style="margin-top:24px">${esc(p.howTitle)}</h2>
<ul class="how">${p.how.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>
</section>
</main>
<footer class="foot"><div class="wrap row">
<a href="/terms">${esc(t.common.terms)}</a>
<a href="/privacy">${esc(t.common.privacy)}</a>
<a href="mailto:support&#64;kvist.site">support<span>&#64;</span>kvist.site</a>
<a href="https://t.me/brmalov">@brmalov</a>
<a href="https://kvist.site">${esc(t.common.site)}</a>
<a href="https://github.com/davud-d/Kvist/tree/main/cloud">${esc(t.common.source)}</a>
</div></footer>
</body>
</html>
`;
}
