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
const bridge = ({ platform, wallet, pay, signin }) => {
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
      if (name === "signIn") {
        answer = signin === "none" ? null : { started: true };
        // The app's real round trip (system browser, code, exchange) ends with the web view signed in: with a dev server
        // that is the dev sign-in. "hold" stays where it is, to see what the page asked for.
        if (signin === "app") setTimeout(() => location.assign("/auth/github/login?next=%2Fm%2F"), 30);
      }
      if (name === "pay") answer = pay === "cancel" ? { cancelled: true } : { token: wallet === "apple_pay" ? apple : google };
      setTimeout(() => window.__kvistReply(id, JSON.stringify(answer)), 10);
    },
  };
};

async function openPage(profile, server, { host = true, pay = "ok", signin = "app", locale = "en-US", viewport } = {}) {
  const browser = await browserFor(profile);
  const context = await browser.newContext({ ...pw.devices[profile.device], locale, ...(viewport ? { viewport } : {}) });
  if (host) await context.addInitScript(bridge, { platform: profile.platform, wallet: profile.wallet, pay, signin });
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
  // What is missing is said once, above the field, with the button that fixes it.
  await page.locator(".banner-action").click();
  await page.locator(".plan-card", { hasText: "Trial" }).click();
  await page.locator(".sheet").waitFor({ state: "detached" });
}

async function addKeyAndProject(page) {
  await page.locator(".banner-action").click();
  await page.locator(".picker").click();
  await page.locator("input[type=search]").fill("other");
  await page.locator(".sheet .choice", { hasText: "Other" }).click();
  await page.locator("input[type=url]").fill("http://127.0.0.1:8799/v1");
  await page.locator("input[placeholder='Model name']").fill("fake-model");
  await page.locator("input[type=password]").fill("sk-test-12345678");
  await page.locator(".sheet .btn.primary").click();
  await page.locator(".sheet").waitFor({ state: "detached" });
  // The project is the bar's title; tapping it opens the projects and a way to make a new one.
  await page.locator(".title-btn").click();
  await page.locator(".pop-item", { hasText: "New project" }).click();
  await page.locator("input[placeholder='Project name']").fill("demo");
  await page.locator(".sheet .btn.primary").click();
  await page.locator(".sheet").waitFor({ state: "detached" });
  await page.locator(".title-btn", { hasText: "demo" }).waitFor();
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
  assert.match(await page.locator(".banner").innerText(), /plan/i, "what is missing is said above the field");
  await shot(page, "home-setup");
  await startTrial(page);
  await addKeyAndProject(page);
  assert.equal(await page.locator(".banner").count(), 0, "nothing left to say");
  await shot(page, "home-ready");
  await sendTask(page, "quick");
  await page.getByText("Done: wrote hello.txt.").waitFor({ timeout: 30000 });
  // One call stands on its own, with no group head, as on the web.
  await visible(page, ".item-call .call-label");
  assert.equal(await page.locator(".item-call .call-label").first().innerText(), "writes hello.txt");
  assert.equal(await page.locator(".work-head").first().isVisible(), false);
  await shot(page, "chat");
  assert.equal((await page.locator(".bar-title .t1").innerText()).trim(), "demo", "the bar says only the project");
  assert.equal(await page.locator(".glass-pill").isVisible(), true, "a session has its new-session and more buttons");
  // The address is the session: a reload comes back to it with its history.
  await page.reload();
  await page.getByText("Done: wrote hello.txt.").waitFor({ timeout: 15000 });
  assert.ok(await page.locator(".item-user").count() === 1);
  // The drawer lists it and the hours left show at its foot.
  await page.getByLabel("Sessions").first().click();
  await visible(page, ".session.on");
  assert.match(await page.locator(".drawer-account").innerText(), /left/);
  await shot(page, "drawer");
  assert.deepEqual(page.problems, []);
});

scenario("in the app, signing in is handed to the app (GitHub's page will not run in a web view)", async (page) => {
  await page.goto(`${page.base}/m/`);
  await page.getByText("Continue with GitHub").click();
  await page.waitForTimeout(800);
  assert.equal((await hostCalls(page, "signIn")).length, 1);
  assert.match(page.url(), /\/m\/$/, "the web view itself did not go to GitHub");
  assert.deepEqual(page.problems, []);
});

scenario("a host that cannot sign in falls back to the ordinary redirect", async (page) => {
  await page.goto(`${page.base}/m/`);
  await page.getByText("Continue with GitHub").click();
  // The host answered "cannot": the page went the ordinary way, and signing in worked (the terms follow).
  await visible(page, "#t1");
});

scenario("a photo is shrunk in the browser, sent with the task, and shown in the message", async (page) => {
  await signIn(page);
  await startTrial(page);
  await addKeyAndProject(page);
  // A large picture: the page must send a small JPEG, not this.
  const big = await page.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 3000; canvas.height = 2000;
    const context = canvas.getContext("2d");
    context.fillStyle = "#c33"; context.fillRect(0, 0, 3000, 2000);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    return Array.from(new Uint8Array(await blob.arrayBuffer()));
  });
  const sentBodies = [];
  page.on("request", (request) => request.url().endsWith("/api/tasks") && request.method() === "POST" && sentBodies.push(request.postData()));
  await page.locator("input[type=file]").first().setInputFiles({ name: "IMG_0001.PNG", mimeType: "image/png", buffer: Buffer.from(big) });
  await visible(page, ".thumb img");
  await page.locator(".composer-input").fill("what is this");
  await page.locator(".composer .round").click();
  await page.waitForURL(/\/m\/s\//);
  await visible(page, ".item-user .user-image");
  const goal = JSON.parse(JSON.parse(sentBodies[0]).goal);
  assert.equal(goal.type, "kvist-image-goal");
  assert.match(goal.images[0].data_url, /^data:image\/jpeg;base64,/);
  assert.match(goal.images[0].name, /^photo-[a-z0-9]+-\d+\.jpg$/, "the file's own name is not sent");
  const bytes = Buffer.from(goal.images[0].data_url.split(",")[1], "base64");
  assert.ok(bytes.length < 300_000, `a small JPEG, not ${bytes.length} bytes`);
  assert.equal(bytes[0], 0xff);
  assert.match(await page.locator(".item-user").first().innerText(), /what is this/);
  await page.getByText("Done: wrote hello.txt.").waitFor({ timeout: 30000 });
  assert.deepEqual(page.problems, []);
});

scenario("the gauge by the send button sets how hard the model thinks, like the web's slider", async (page) => {
  await signIn(page);
  await startTrial(page);
  assert.equal(await page.locator(".gauge-btn").isVisible(), false, "no model, nothing to set");
  await addKeyAndProject(page);
  await page.locator(".gauge-btn").click();
  await visible(page, ".dial-track");
  assert.equal(await page.locator(".dial-word").innerText(), "Medium");
  await shot(page, "dial");
  const box = await page.locator(".dial-track").boundingBox();
  // A tap near the far end goes to the last stop; the choice is saved when the finger lets go.
  await page.mouse.click(box.x + box.width - 6, box.y + box.height / 2);
  assert.equal(await page.locator(".dial-word").innerText(), "Max");
  await page.waitForFunction(async () => (await (await fetch("/api/model-key")).json()).effort === "max");
  await page.locator(".dial-reset").click();
  assert.equal(await page.locator(".dial-word").innerText(), "Medium");
  await page.waitForFunction(async () => (await (await fetch("/api/model-key")).json()).effort === "medium");
  assert.deepEqual(page.problems, []);
});

scenario("the composer is one compact row, typing or not; the model button opens a dropdown, not a sheet", async (page) => {
  await signIn(page);
  await startTrial(page);
  await addKeyAndProject(page);
  const field = page.locator(".composer-input");
  const height = async () => (await page.locator(".composer").boundingBox()).height;
  const resting = await height();
  assert.ok(resting <= 48, `a compact box: ${resting}`);
  await field.focus();
  await field.fill("a few words");
  assert.ok(Math.abs(await height() - resting) < 1, `typing does not change its size: ${resting} -> ${await height()}`);
  const plus = await page.locator(".composer .plus").boundingBox();
  const input = await field.boundingBox();
  assert.ok(Math.abs(plus.y + plus.height / 2 - (input.y + input.height / 2)) < 12, "plus and field share one row");
  await shot(page, "typing");
  await field.fill("");
  await field.blur();
  assert.ok(Math.abs(await height() - resting) < 1, `letting go of the field does not grow it: ${resting} -> ${await height()}`);
  await page.locator(".chip.small").first().click();
  await visible(page, ".model-menu");
  assert.equal(await page.locator(".sheet").count(), 0, "a dropdown, not a bottom sheet");
  await shot(page, "model-menu");
  assert.deepEqual(page.problems, []);
});

scenario("the conversation is the web's: a thought on its own, a run of calls in a group that folds, the orb at the end of the words", async (page) => {
  await signIn(page);
  await startTrial(page);
  await addKeyAndProject(page);
  await sendTask(page, "steps");
  await visible(page, ".item-thought");
  await visible(page, ".item-work .work-head");
  // While the answer is being written the orb stands at the end of its words, and there is no caret.
  await page.locator(".item-reply .md .orb").waitFor({ timeout: 15000 });
  await shot(page, "streaming");
  assert.equal(await page.locator(".item-thought .item-call, .item-work .item-thought").count(), 0, "the thought is not inside the calls");
  assert.equal(await page.locator(".item-work .item-call").count(), 3);
  await page.locator(".item-final, .item-reply:not(.live)").last().waitFor({ timeout: 15000 });
  // Two seconds after the turn ends the group folds into its head line.
  await page.waitForFunction(() => document.querySelector(".item-work .work-body")?.hidden === true, null, { timeout: 8000 });
  await shot(page, "folded");
  await page.locator(".item-work .work-head").click();
  await page.waitForFunction(() => document.querySelector(".item-work .work-body")?.hidden === false);
  // Nothing on the page is wider than the screen: it scrolls up and down only.
  assert.equal(await page.evaluate(() => document.querySelector(".feed").scrollWidth <= document.querySelector(".feed").clientWidth), true);
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
  await visible(page, ".tail .orb");
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
  await page.getByText("Дай Kvist задачу").waitFor({ timeout: 15000 });
  const text = await page.locator("body").innerText();
  assert.match(text, /Дай Kvist задачу/);
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
    const page = await openPage(profile, server, { host: !/plain browser/.test(name), signin: /cannot sign in/.test(name) ? "none" : /handed to the app/.test(name) ? "hold" : "app" });
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
