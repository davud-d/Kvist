// The phone app in a real browser, against a real cloud server (tests/server.py: a real runner, the scripted Kvist, a
// fake Stripe). Chromium with an Android phone's profile always; WebKit (the engine of every iPhone browser) with an
// iPhone's when WEBKIT=1. The phone apps' own bridge is a small stand-in the page cannot tell from the real one.
//
//   PLAYWRIGHT_MODULE=<path to playwright/index.mjs>  CHROMIUM_EXECUTABLE=<chrome>  PYTHON=<python>  node run.mjs
//   KVIST_ONLY='<regex over scenario names>'   runs a subset;   SHOTS=<dir>   keeps a screenshot per scenario
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const pw = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const here = fileURLToPath(new URL(".", import.meta.url));
const SERVER = join(here, "..", "server.py");
const ONLY = process.env.KVIST_ONLY ? new RegExp(process.env.KVIST_ONLY) : null;
const SHOTS = process.env.SHOTS;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

// -- the server --------------------------------------------------------------------------------------------------

async function startServer(extra = []) {
  const data = mkdtempSync(join(tmpdir(), "kvist-mobile-"));
  const port = 20000 + Math.floor(Math.random() * 20000);
  const child = spawn(process.env.PYTHON || "python3", ["-u", SERVER, "--port", String(port), "--data", data, ...extra], { stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stderr.on("data", (chunk) => (log += chunk));
  const url = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`server did not start:\n${log}`)), 30000);
    child.stdout.on("data", (chunk) => {
      const match = /READY (\S+)/.exec(String(chunk));
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    child.on("exit", (code) => reject(new Error(`server exited ${code}:\n${log}`)));
  });
  return {
    url,
    log: () => log,
    async stop() {
      child.removeAllListeners("exit");
      child.kill("SIGTERM");
      await new Promise((resolve) => child.once("exit", resolve));
      rmSync(data, { recursive: true, force: true });
    },
  };
}

// -- the browsers ---------------------------------------------------------------------------------------------------

const PROFILES = [
  { name: "android", browser: "chromium", device: "Pixel 7", platform: "android", wallet: "google_pay" },
  ...(process.env.WEBKIT === "1" ? [{ name: "iphone", browser: "webkit", device: "iPhone 14", platform: "ios", wallet: "apple_pay" }] : []),
];

const launched = new Map();
async function browserFor(profile) {
  if (!launched.has(profile.browser)) {
    const options = profile.browser === "chromium" && process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {};
    launched.set(profile.browser, await pw[profile.browser].launch(options));
  }
  return launched.get(profile.browser);
}

/**
 * The phone app's side of the bridge. `pay` says what the operating system's sheet does: "ok" hands back a wallet's
 * token, "cancel" is the person closing the sheet, "none" means this phone has no wallet.
 */
const bridge = ({ platform, wallet, pay }) => {
  window.__host = [];
  const google = JSON.stringify({ id: "tok_goodGooglePayToken01", object: "token", type: "card" });
  const apple = JSON.stringify({ paymentData: '{"version":"EC_v1","data":"x"}', transactionIdentifier: "tx-1", network: "Visa", displayName: "Visa 4242" });
  window.KvistHost = {
    platform,
    call(id, name, json) {
      const args = JSON.parse(json);
      window.__host.push({ name, args });
      let answer = null;
      if (name === "wallets") answer = pay === "none" ? [] : [wallet];
      if (name === "payDone") answer = null;
      if (name === "pay") answer = pay === "cancel" ? { cancelled: true } : { token: wallet === "apple_pay" ? apple : google };
      setTimeout(() => window.__kvistReply(id, JSON.stringify(answer)), 10);
    },
  };
};

async function openPage(profile, server, { host = true, pay = "ok", locale = "en-US", viewport } = {}) {
  const browser = await browserFor(profile);
  const context = await browser.newContext({ ...pw.devices[profile.device], locale, ...(viewport ? { viewport } : {}) });
  if (host) await context.addInitScript(bridge, { platform: profile.platform, wallet: profile.wallet, pay });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    // The 401 before signing in is how the page learns it has to; anything else at error level is a bug.
    if (message.type() === "error" && !/401 \(Unauthorized\)|net::ERR_FAILED|Viewport argument key/.test(message.text())) problems.push(`console: ${message.text()}`);
  });
  // Google's button script is the one thing fetched from outside; tests must not depend on the network.
  await page.route("https://pay.google.com/**", (route) => route.abort());
  await page.route("https://www.gstatic.com/**", (route) => route.abort());
  page.problems = problems;
  page.base = server.url;
  return page;
}

const visible = (page, selector) => page.locator(selector).first().waitFor({ state: "visible", timeout: 15000 });
const shot = async (page, name) => SHOTS && (await page.waitForTimeout(250), page.screenshot({ path: join(SHOTS, `${name}.png`) }));

/** Sign in as the dev user and accept the terms; leaves the page on the start screen. */
async function signIn(page) {
  await page.goto(`${page.base}/m/`);
  await page.getByText("Continue with GitHub").click();
  await visible(page, "#t1");
  await page.check("#t1");
  await page.check("#t2");
  await page.locator(".gate .btn.primary").click();
  await visible(page, ".home");
}

async function startTrial(page) {
  await page.locator(".setup .row", { hasText: "Pick a plan" }).click();
  await page.locator(".plan-card", { hasText: "Trial" }).click();
  await page.locator(".sheet").waitFor({ state: "detached" });
}

async function addKeyAndProject(page) {
  await page.locator(".setup .row", { hasText: "Add a model key" }).click();
  await page.locator(".picker").click();
  await page.locator("input[type=search]").fill("other");
  await page.locator(".sheet .choice", { hasText: "Other" }).click();
  await page.locator("input[type=url]").fill("http://127.0.0.1:8799/v1");
  await page.locator("input[placeholder='Model name']").fill("fake-model");
  await page.locator("input[type=password]").fill("sk-test-12345678");
  await page.locator(".sheet .btn.primary").click();
  await page.locator(".sheet").waitFor({ state: "detached" });
  await page.locator(".setup .row", { hasText: "Create a project" }).click();
  await page.locator("input[placeholder='Project name']").fill("demo");
  await page.locator(".sheet .btn.primary").click();
  await page.locator(".sheet").waitFor({ state: "detached" });
  await visible(page, ".suggest");
}

async function sendTask(page, text) {
  await page.locator(".composer-input").fill(text);
  await page.locator(".composer .round").click();
  await page.waitForURL(/\/m\/s\//);
}

const hostCalls = (page, name) => page.evaluate((n) => window.__host.filter((c) => c.name === n), name);
const charges = async (page) => (await (await fetch(`${page.base}/__test/charges`)).json()).charges;
const hoursLeft = (page) => page.evaluate(async () => (await (await fetch("/api/billing/subscription")).json()).balance_seconds / 3600);

// -- scenarios ---------------------------------------------------------------------------------------------------------

const scenarios = [];
const scenario = (name, fn) => scenarios.push({ name, fn });

scenario("first run: sign in, terms, trial, key, project, first task", async (page, profile) => {
  await page.goto(`${page.base}/m/`);
  await visible(page, ".gate h1");
  await shot(page, "gate");
  await page.getByText("Continue with GitHub").click();
  await visible(page, "#t1");
  assert.equal(await page.locator(".gate .btn.primary").isDisabled(), true, "the button waits for both boxes");
  await page.check("#t1");
  assert.equal(await page.locator(".gate .btn.primary").isDisabled(), true, "one box is not enough");
  await page.check("#t2");
  await page.locator(".gate .btn.primary").click();
  await visible(page, ".home");
  assert.equal(await page.locator(".setup .row").count(), 3, "three things to set up");
  await shot(page, "home-setup");
  await startTrial(page);
  await addKeyAndProject(page);
  assert.equal(await page.locator(".setup").count(), 0, "nothing left to set up");
  await shot(page, "home-ready");
  await sendTask(page, "quick");
  await page.getByText("Done: wrote hello.txt.").waitFor({ timeout: 30000 });
  assert.match(await page.locator(".work-title").first().innerText(), /Worked for .* · 1 step$/);
  await page.locator(".work-head").first().click();
  await visible(page, ".call-label");
  assert.equal(await page.locator(".call-label").first().innerText(), "writes hello.txt");
  await shot(page, "chat");
  assert.match(await page.locator(".bar-title .t1").innerText(), /quick/);
  // The address is the session: a reload comes back to it with its history.
  await page.reload();
  await page.getByText("Done: wrote hello.txt.").waitFor({ timeout: 15000 });
  assert.ok(await page.locator(".msg.user").count() === 1);
  // The drawer lists it and the hours left show at its foot.
  await page.getByLabel("Sessions").first().click();
  await visible(page, ".session.on");
  assert.match(await page.locator(".drawer-account").innerText(), /left/);
  await shot(page, "drawer");
  assert.deepEqual(page.problems, []);
});

scenario("the agent asks, the person answers", async (page) => {
  await signIn(page);
  await startTrial(page);
  await addKeyAndProject(page);
  await sendTask(page, "ask");
  await visible(page, ".dialog");
  assert.match(await page.locator(".dialog-title").innerText(), /rm -rf build/);
  assert.equal(await page.locator(".composer").isHidden(), true, "the question stands where the composer was");
  await shot(page, "dialog");
  await page.locator(".dialog .btn.primary").click();
  await page.locator(".dialog").waitFor({ state: "detached" });
  await visible(page, ".composer");
  assert.deepEqual(page.problems, []);
});

scenario("the round button is send, then pause while it works", async (page) => {
  await signIn(page);
  await startTrial(page);
  await addKeyAndProject(page);
  await sendTask(page, "hang");
  await visible(page, ".round.stop");
  assert.match(await page.locator(".status-line").innerText(), /Working/);
  await shot(page, "working");
  await page.locator(".round.stop").click();
  await visible(page, ".banner");
  assert.match(await page.locator(".banner").innerText(), /Paused/);
  assert.equal(await page.locator(".round.stop").count(), 0);
  assert.deepEqual(page.problems, []);
});

for (const kind of ["google_pay", "apple_pay"]) {
  scenario(`${kind}: the OS sheet gets the server's price, the plan is bought`, async (page, profile) => {
    if (profile.wallet !== kind) return "skipped";
    await signIn(page);
    await startTrial(page);
    await page.getByLabel("Sessions").first().click();
    await page.locator(".drawer-account").click();
    await page.getByText("Plans", { exact: true }).first().click();
    await page.locator(".plan-card", { hasText: "Start" }).click();
    await visible(page, ".pay-price");
    assert.equal(await page.locator(".pay-price").innerText(), "$15");
    const button = kind === "apple_pay" ? ".apple-pay-button" : ".google-pay-slot button";
    await visible(page, button);
    await shot(page, `pay-${kind}`);
    await page.locator(button).first().click();
    await page.getByText("Paid. Thank you.").waitFor({ timeout: 15000 });
    const [pay] = await hostCalls(page, "pay");
    assert.deepEqual((await hostCalls(page, "payDone")).map((c) => c.args.ok), [true], "the sheet is told the charge went through");
    assert.equal(pay.args.wallet, kind);
    if (kind === "google_pay") {
      assert.equal(pay.args.request.transactionInfo.totalPrice, "15.00");
      assert.equal(pay.args.request.allowedPaymentMethods[0].tokenizationSpecification.parameters.gateway, "stripe");
    } else {
      assert.equal(pay.args.request.summaryItems.at(-1).amount, "15.00");
      assert.equal(pay.args.request.merchantIdentifier, "merchant.site.kvist.cloud");
    }
    const [charge] = await charges(page);
    assert.equal(charge.amount, "1500");
    assert.equal(charge.currency, "usd");
    assert.equal(await hoursLeft(page), 50);
    assert.deepEqual(page.problems, []);
  });
}

scenario("a declined card says so and charges nothing; closing the sheet is not an error", async (page, profile) => {
  await signIn(page);
  await startTrial(page);
  await page.getByLabel("Sessions").first().click();
  await page.locator(".drawer-account").click();
  await page.getByText("Plans", { exact: true }).first().click();
  await page.locator(".plan-card", { hasText: "Pro" }).click();
  const button = profile.wallet === "apple_pay" ? ".apple-pay-button" : ".google-pay-slot button";
  await visible(page, button);
  assert.equal((await fetch(`${page.base}/__test/card/declined`, { method: "POST" })).status, 200);
  await page.locator(button).first().click();
  await visible(page, ".pay-status");
  assert.match(await page.locator(".pay-status").innerText(), /declined/i);
  assert.deepEqual((await hostCalls(page, "payDone")).map((c) => c.args.ok), [false], "the sheet is told it failed");
  assert.equal(await hoursLeft(page), 5, "still on the trial");
  await shot(page, "pay-declined");
  // The person closes the sheet instead: nothing is said, nothing is charged.
  await page.reload();
  await page.getByLabel("Sessions").first().click();
  await page.locator(".drawer-account").click();
  await page.getByText("Plans", { exact: true }).first().click();
  await page.locator(".plan-card", { hasText: "Pro" }).click();
  await visible(page, button);
  const before = (await charges(page)).length;
  await page.evaluate(() => {
    const call = window.KvistHost.call;
    window.KvistHost.call = (id, name, json) => (name === "pay" ? setTimeout(() => window.__kvistReply(id, JSON.stringify({ cancelled: true })), 10) : call(id, name, json));
  });
  await page.locator(button).first().click();
  await page.waitForTimeout(600);
  assert.equal((await page.locator(".pay-status").innerText()).trim(), "");
  assert.equal((await charges(page)).length, before, "no new charge was asked for");
});

scenario("a plain browser has no wallet and says nothing false about it", async (page) => {
  await signIn(page);
  await startTrial(page);
  await page.getByLabel("Sessions").first().click();
  await page.locator(".drawer-account").click();
  await page.getByText("Plans", { exact: true }).first().click();
  await page.locator(".plan-card", { hasText: "Start" }).click();
  await visible(page, ".pay-price");
  assert.equal(await page.locator(".apple-pay-button, .google-pay-slot").count(), 0);
  assert.equal(await page.locator(".row.dev").count(), 1, "only the development path to pay on a local run");
});

scenario("Russian: every word is Russian and nothing is left unfilled", async (page) => {
  await signIn(page);
  await page.getByLabel("Sessions").first().click();
  await page.locator(".drawer-account").click();
  await page.getByText("RU", { exact: true }).click();
  // The page reloads itself in the new language; WebKit is slower to get there than Chromium.
  await page.getByText("Поставь агенту задачу").waitFor({ timeout: 15000 });
  const text = await page.locator("body").innerText();
  assert.match(text, /Поставь агенту задачу/);
  assert.doesNotMatch(text, /undefined|\{\w+\}|\[object/);
  await shot(page, "home-ru");
});

scenario("it fits every phone: no sideways scroll, nothing wider than the screen", async (page, profile) => {
  await signIn(page);
  await startTrial(page);
  const sizes = [[320, 568], [360, 640], [390, 844], [430, 932]];
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    for (const view of ["home", "plans", "account"]) {
      if (view === "plans") {
        await page.getByLabel("Sessions").first().click();
        await page.locator(".drawer-account").click();
        await page.getByText("Plans", { exact: true }).first().click();
      }
      if (view === "account") await page.locator(".sheet-head .icon-btn").last().click();
      await page.waitForTimeout(350);
      const wide = await page.evaluate(() => {
        const limit = window.innerWidth;
        const over = [...document.querySelectorAll("body *")].filter((el) => {
          const box = el.getBoundingClientRect();
          return box.width > 0 && (box.right > limit + 1 || box.left < -1) && !el.closest(".drawer, .drawer-root:not(.open), .sheet:not(.in), .scrim:not(.in), #toasts");
        });
        return { scroll: document.documentElement.scrollWidth - limit, over: over.slice(0, 3).map((el) => `${el.tagName}.${el.className}`) };
      });
      assert.ok(wide.scroll <= 0, `${view} at ${width}: page scrolls sideways by ${wide.scroll}`);
      assert.deepEqual(wide.over, [], `${view} at ${width}: wider than the screen`);
    }
    await page.evaluate(() => document.querySelectorAll(".sheet").forEach((s) => s.remove()));
    await page.evaluate(() => document.querySelectorAll(".scrim").forEach((s) => s.remove()));
  }
  assert.deepEqual(page.problems, []);
});

// -- running them ---------------------------------------------------------------------------------------------------------

let failed = 0;
for (const profile of PROFILES) {
  for (const { name, fn } of scenarios) {
    const label = `${profile.name}: ${name}`;
    if (ONLY && !ONLY.test(label)) continue;
    const server = await startServer();
    const page = await openPage(profile, server, { host: !/plain browser/.test(name) });
    const started = Date.now();
    try {
      page.on("response", (r) => r.url().includes("/api/wallet") && console.log(`     ${r.status()} ${r.url().replace(page.base, "")}`));
      const result = await fn(page, profile);
      console.log(`${result === "skipped" ? "skip" : "ok  "} ${label} (${Date.now() - started} ms)`);
    } catch (error) {
      failed++;
      console.log(`FAIL ${label}\n     ${String(error.stack ?? error).split("\n").slice(0, 6).join("\n     ")}`);
      if (SHOTS) await page.screenshot({ path: join(SHOTS, `FAIL-${profile.name}-${name.slice(0, 30).replace(/\W+/g, "-")}.png`) }).catch(() => {});
      if (server.log()) console.log(`     server log: ${server.log().slice(-600)}`);
      console.log(`     page: ${(await page.locator("body").innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 500)}`);
      console.log(`     problems: ${JSON.stringify(page.problems)}`);
    } finally {
      await page.context().close();
      await server.stop();
    }
  }
}
for (const browser of launched.values()) await browser.close();
console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
