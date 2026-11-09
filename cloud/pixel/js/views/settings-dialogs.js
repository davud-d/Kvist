// The settings that need a window of their own: the model key. The key is checked the moment it is saved,
// with one cheap request through the server, and the answer is said in plain words next to the field.
// The server keeps the key encrypted and shows only its last four characters, ever again.

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

export function openKeyDialog() {
  const saved = () => (account.key?.provider === "chatgpt" ? account.key : null);
  const draft = account.key?.set ? {} : readDraft();
  const remember = () => {
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ provider: choice.provider, base_url: base.value.trim(), model: model.value.trim() }));
    } catch {
      // Storage blocked: the draft is only a convenience.
    }
  };
  const choice = { provider: account.key?.set ? account.key.provider : draft.provider || "deepseek" };
  const key = h("input", { class: "field", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "sk-…" });
  const base = h("input", { class: "field", type: "url", placeholder: "https://…", spellcheck: "false", value: account.key?.base_url ?? draft.base_url ?? "" });
  const model = h("input", { class: "field", type: "text", placeholder: t("model name"), spellcheck: "false", value: account.key?.model ?? draft.model ?? "" });
  base.addEventListener("input", () => remember());
  model.addEventListener("input", () => remember());
  const status = h("p", { class: "dim", "aria-live": "polite" });
  const providers = h("div", { class: "segment" });
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
    fill(providers, list.map((item) => h("button", { class: `seg${item.partner ? " partner" : ""}${item.id === choice.provider ? " on" : ""}`, type: "button", onclick: () => { choice.provider = item.id; remember(); paint(); } }, item.id === "other" ? t("other") : item.label)));
    const current = list.find((item) => item.id === choice.provider);
    fill(link, current?.keys_page ? h("a", { href: current.keys_page, target: "_blank", rel: "noopener" }, `${t("get a key")} ↗`) : null);
    // A sign-in has no key to paste and nothing to save: its own button does the work.
    const signIn = Boolean(current?.sign_in);
    key.style.display = save.style.display = signIn ? "none" : "";
    fill(extra, signIn ? chatgptRows() : [current?.needs_base_url ? base : null, current?.needs_model ? model : null].filter(Boolean));
  };

  save.addEventListener("click", async () => {
    if (key.value.trim().length < 8) return key.focus();
    save.disabled = true;
    status.textContent = t("checking key");
    try {
      const result = await api("/api/model-key", { method: "POST", body: { provider: choice.provider, key: key.value.trim(), base_url: base.value.trim(), model: model.value.trim() } });
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
