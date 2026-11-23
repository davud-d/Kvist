// The settings that need a window of their own: the model key. The key is checked the moment it is saved,
// with one cheap request through the server, and the answer is said in plain words next to the field.
// The server keeps the key encrypted and shows only its last four characters, ever again.

import { renameTask } from "../actions.js";
import { account, api, refreshKey } from "../cloud.js";
import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { openModal } from "./modal.js";

const DRAFT_KEY = "kvist.keyDraft";

// What was picked survives a reload; the key itself never does (it is saved the moment it is pasted).
function readDraft() {
  try {
    return JSON.parse(sessionStorage.getItem(DRAFT_KEY)) ?? {};
  } catch {
    // Storage blocked or the entry is not JSON: start without a draft.
    return {};
  }
}

/** The name the task shows in the rail and above the conversation; leave it empty to go back to the first line. */
export function openRename(agent) {
  const input = h("input", { class: "field", type: "text", maxlength: 120, spellcheck: "false", value: agent.title ?? "" });
  const go = () => {
    renameTask(agent, input.value);
    modal.close();
  };
  input.addEventListener("keydown", (event) => event.key === "Enter" && go());
  const modal = openModal({
    title: t("rename"),
    content: [input, h("div", { class: "dialog-buttons" }, h("button", { class: "btn primary", type: "button", onclick: go }, t("ok")), h("button", { class: "btn", type: "button", onclick: () => modal.close() }, t("cancel")))],
  });
  input.focus();
}

// How many providers get a button of their own after the partner; the rest are one pick in a list.
const SHOWN_PROVIDERS = 3;

// The partner on a row of its own, the usual providers and "Other" as buttons, the long tail in a select.
function providerPicker(list, current, choose) {
  const seg = (item) => h("button", { class: `seg${item.partner ? " partner" : ""}${item.id === current ? " on" : ""}`, type: "button", onclick: () => choose(item.id) }, item.id === "other" ? t("other") : item.label);
  const partners = list.filter((item) => item.partner);
  const usual = list.filter((item) => !item.partner && item.id !== "other");
  const other = list.find((item) => item.id === "other");
  const shown = usual.slice(0, SHOWN_PROVIDERS);
  const rest = usual.slice(SHOWN_PROVIDERS);
  const picked = rest.some((item) => item.id === current);
  const select = rest.length
    ? h("select", { class: "field", "aria-label": t("other providers"), onchange: (event) => event.target.value && choose(event.target.value) },
      h("option", { value: "", selected: !picked }, `${t("other providers")}…`),
      rest.map((item) => h("option", { value: item.id, selected: item.id === current }, item.label)))
    : null;
  return [
    partners.length ? h("div", { class: "segment provider-row" }, partners.map(seg)) : null,
    h("div", { class: "segment provider-row" }, [...shown, ...(other ? [other] : [])].map(seg)),
    select,
  ];
}

/** `preferred`: the provider to start on (the model picker's "Connect key" under a provider). */
export function openKeyDialog(preferred = "") {
  const saved = () => (account.key?.provider === "chatgpt" ? account.key : null);
  const draft = account.key?.set ? {} : readDraft();
  const remember = () => {
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ provider: choice.provider, base_url: base.value.trim(), model: model.value.trim() }));
    } catch {
      // Storage blocked: the draft is only a convenience.
    }
  };
  const choice = { provider: preferred || (account.key?.set ? account.key.provider : draft.provider || "deepseek") };
  const key = h("input", { class: "field", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "sk-…" });
  const base = h("input", { class: "field", type: "url", placeholder: "https://…", spellcheck: "false", value: account.key?.base_url ?? draft.base_url ?? "" });
  const model = h("input", { class: "field", type: "text", placeholder: t("model name"), spellcheck: "false", value: account.key?.model ?? draft.model ?? "" });
  // One "Name: value" per line. Not in the draft: a value may be a secret.
  const headers = h("textarea", { class: "field", rows: 3, spellcheck: "false", placeholder: "x-opencode-session: {session}" });
  // Streaming shows a file as the model writes it; a few gateways cannot do it, then this is switched off.
  const stream = h("input", { type: "checkbox", id: "key-stream", checked: account.key?.stream !== false });
  const streamRow = () => h("label", { class: "dim", for: "key-stream" }, stream, ` ${t("stream answers")}`);
  const parseHeaders = () => {
    const found = {};
    for (const line of headers.value.split("\n")) {
      if (!line.trim()) continue;
      const colon = line.indexOf(":");
      if (colon < 1) return null;
      found[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
    }
    return found;
  };
  const headersBlock = () => h("details", {}, h("summary", {}, t("extra headers")),
    h("p", { class: "dim" }, t("extra headers hint")),
    account.key?.headers?.length ? h("p", { class: "dim" }, t("extra headers saved").replace("{names}", account.key.headers.join(", "))) : null,
    headers);
  base.addEventListener("input", () => remember());
  model.addEventListener("input", () => remember());
  const status = h("p", { class: "dim", "aria-live": "polite" });
  const providers = h("div", { class: "provider-more" });
  const extra = h("div", { class: "field-stack" });
  const link = h("p", { class: "dim" });
  const save = h("button", { class: "btn primary", type: "button" }, t("save"));
  const modal = openModal({
    title: t("model key"),
    content: [providers, link, extra, key, status, h("div", { class: "dialog-buttons" }, save, h("button", { class: "btn", type: "button", onclick: () => modal.close() }, t("cancel")))],
  });

  // The plan instead of a key: sign in at OpenAI and come back to this address (the server keeps it good).
  const chatgptRows = () => {
    const state = saved()?.chatgpt_state ?? "";
    if (state === "connected") {
      const email = saved().chatgpt_email;
      const off = h("button", { class: "btn", type: "button" }, t("chatgpt disconnect"));
      off.addEventListener("click", async () => {
        await api("/api/chatgpt", { method: "DELETE" });
        await refreshKey();
        paint();
      });
      return [h("p", { class: "dim" }, `${t("chatgpt connected")}${email ? ` · ${email}` : ""}`), off];
    }
    const start = h("button", { class: "btn primary", type: "button" }, t(state === "needs_login" ? "chatgpt sign in again" : "chatgpt sign in"));
    start.addEventListener("click", async () => {
      start.disabled = true;
      status.textContent = t("chatgpt opening");
      try {
        const { url } = await api("/api/chatgpt/start", { method: "POST", body: { next: location.pathname } });
        if (new URL(url).protocol !== "https:") throw new Error("not https");
        location.assign(url);
      } catch (error) {
        start.disabled = false;
        status.textContent = error.localized ?? String(error);
      }
    });
    return [h("p", { class: "dim" }, t(state === "needs_login" ? "chatgpt needs login" : "chatgpt intro")), start];
  };

  const paint = () => {
    const list = account.providers.length ? account.providers : [{ id: "deepseek", label: "DeepSeek" }, { id: "openai", label: "OpenAI" }, { id: "anthropic", label: "Anthropic" }, { id: "other", label: "…", needs_base_url: true, needs_model: true }];
    fill(providers, providerPicker(list, choice.provider, (id) => { choice.provider = id; remember(); paint(); }));
    const current = list.find((item) => item.id === choice.provider);
    fill(link, current?.keys_page ? h("a", { href: current.keys_page, target: "_blank", rel: "noopener" }, `${t("get a key")} ↗`) : null);
    // A sign-in has no key to paste and nothing to save: its own button does the work.
    const signIn = Boolean(current?.sign_in);
    key.style.display = save.style.display = signIn ? "none" : "";
    fill(extra, signIn ? chatgptRows() : [current?.needs_base_url ? base : null, current?.needs_model ? model : null, current?.needs_base_url ? headersBlock() : null, current?.needs_base_url ? streamRow() : null].filter(Boolean));
  };

  save.addEventListener("click", async () => {
    if (key.value.trim().length < 8) return key.focus();
    const extraHeaders = parseHeaders();
    if (extraHeaders === null) {
      status.textContent = t("bad headers");
      return undefined;
    }
    save.disabled = true;
    status.textContent = t("checking key");
    try {
      const result = await api("/api/model-key", { method: "POST", body: { provider: choice.provider, key: key.value.trim(), base_url: base.value.trim(), model: model.value.trim(), headers: extraHeaders, stream: stream.checked } });
      if (!result.ok) {
        status.textContent = t(result.code);
        return undefined;
      }
      key.value = "";
      await refreshKey();
      modal.close();
    } catch (error) {
      status.textContent = error.localized ?? String(error);
    } finally {
      save.disabled = false;
    }
    return undefined;
  });
  paint();
  return modal;
}
