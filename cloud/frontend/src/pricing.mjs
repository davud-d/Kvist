// The pricing page, rendered at build time per locale. No script: the button is a link, so the
// page works the moment its HTML arrives, also on a slow phone with JavaScript blocked.

const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function duration(minutes, t) {
  if (!minutes) return t.pricing.none;
  if (minutes % 60 === 0) return t.units.h.replace("{h}", minutes / 60);
  return t.units.min.replace("{m}", minutes);
}

function table(t, plans) {
  const p = t.pricing;
  const rows = plans.map((plan) => `<tr>
<th scope="row">${esc(p.planNames[plan.id])}</th>
<td${plan.id === "trial" ? ' class="now"' : ""}>${esc(plan.id === "trial" ? p.trialPerWeek : p.perMonth.replace("{p}", plan.price_usd))}</td>
<td>${plan.hours}</td>
<td>${plan.vcpu} vCPU · ${plan.memory_gb} GB</td>
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
<header class="top"><div class="wrap top-row">
<a class="mark" href="/"><span class="dot" aria-hidden="true"></span>${esc(t.common.brand)}<span class="sub">${esc(t.common.cloud)}</span></a>
<nav class="nav" aria-label="language"><a class="chip" href="/?lang=${other}" hreflang="${other}" lang="${other}" aria-label="${esc(t.common.otherLangName)}">${esc(t.common.otherLang)}</a></nav>
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
<details class="compare"><summary>${esc(p.compare)}</summary>
${table(t, catalog.plans)}
</details>
<h2 class="section-title" style="margin-top:24px">${esc(p.howTitle)}</h2>
<ul class="how">${p.how.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>
</section>
</main>
<footer class="foot"><div class="wrap row">
<a href="https://kvist.site">${esc(t.common.site)}</a>
<a href="https://github.com/davud-d/Kvist/tree/main/cloud">${esc(t.common.source)}</a>
</div></footer>
</body>
</html>
`;
}
