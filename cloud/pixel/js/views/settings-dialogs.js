// The settings that need a window of their own: the model key. The key is checked the moment it is saved,
// with one cheap request through the server, and the answer is said in plain words next to the field.
// The server keeps the key encrypted and shows only its last four characters, ever again.

import { account, api, refreshKey } from "../cloud.js";
import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { openModal } from "./modal.js";

export function openKeyDialog() {
  const choice = { provider: account.key?.set ? account.key.provider : "deepseek" };
  const key = h("input", { class: "field", type: "password", autocomplete: "off", spellcheck: "false", placeholder: "sk-…" });
  const base = h("input", { class: "field", type: "url", placeholder: "https://…", spellcheck: "false", value: account.key?.base_url ?? "" });
  const model = h("input", { class: "field", type: "text", placeholder: t("model name"), spellcheck: "false", value: account.key?.model ?? "" });
  const status = h("p", { class: "dim", "aria-live": "polite" });
  const providers = h("div", { class: "segment" });
  const extra = h("div", { class: "field-stack" });
  const link = h("p", { class: "dim" });
  const save = h("button", { class: "btn primary", type: "button" }, t("save"));
  const modal = openModal({
    title: t("model key"),
    content: [providers, link, extra, key, status, h("div", { class: "dialog-buttons" }, save, h("button", { class: "btn", type: "button", onclick: () => modal.close() }, t("cancel")))],
  });

  const paint = () => {
    const list = account.providers.length ? account.providers : [{ id: "deepseek", label: "DeepSeek" }, { id: "openai", label: "OpenAI" }, { id: "anthropic", label: "Anthropic" }, { id: "other", label: "…" }];
    fill(providers, list.map((item) => h("button", { class: `seg${item.id === choice.provider ? " on" : ""}`, type: "button", onclick: () => { choice.provider = item.id; paint(); } }, item.id === "other" ? t("other") : item.label)));
    const current = list.find((item) => item.id === choice.provider);
    fill(link, current?.keys_page ? h("a", { href: current.keys_page, target: "_blank", rel: "noopener" }, `${t("get a key")} ↗`) : null);
    fill(extra, choice.provider === "other" ? [base, model] : null);
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
