// The model key: which provider, the key itself (it is checked against the provider before it is kept), and for the
// providers that need one, an address and a model name. The key is never shown again, only its last four characters.

import { account, refresh } from "../account.js";
import { post, api } from "../api.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import * as native from "../native.js";
import { openSheet, toast } from "../sheet.js";

/** A second sheet over the first: the providers are many (forty and more), so they are searched, not scrolled. */
function pickProvider(providers, chosen, onPick) {
  openSheet({
    title: t("provider"),
    variant: "tall",
    build: (close) => {
      const search = h("input", { class: "field", type: "search", placeholder: t("search"), autocapitalize: "off", spellcheck: "false", enterkeyhint: "search" });
      const list = h("div", { class: "choices" });
      const paint = () => {
        const needle = search.value.trim().toLowerCase();
        fill(list, providers.filter((p) => `${p.label} ${p.id}`.toLowerCase().includes(needle)).map((p) =>
          h("button", { class: `choice${p.id === chosen.id ? " on" : ""}`, type: "button", onclick: () => {
            onPick(p);
            close();
          } }, h("span", {}, p.label), p.id === chosen.id ? icon("check", 18) : null)));
      };
      search.addEventListener("input", paint);
      paint();
      return [search, list];
    },
  });
}

export function keySheet(then = () => {}, providerId = "") {
  openSheet({
    title: t("model key"),
    variant: "tall",
    build: (close) => {
      const providers = account.providers.filter((p) => !p.sign_in);
      let chosen = providers.find((p) => p.id === (providerId || account.key?.provider)) ?? providers[0];
      const key = h("input", { class: "field", type: "password", autocomplete: "off", autocapitalize: "off", spellcheck: "false",
        placeholder: account.key?.set ? `•••• ${account.key.last4}` : t("key placeholder"), enterkeyhint: "done" });
      const base = h("input", { class: "field", type: "url", inputmode: "url", autocapitalize: "off", spellcheck: "false",
        placeholder: "https://…/v1", value: account.key?.base_url ?? "" });
      const model = h("input", { class: "field", autocapitalize: "off", spellcheck: "false", placeholder: t("model name"),
        value: account.key?.model ?? "" });
      const message = h("p", { class: "pay-status", role: "status" });
      const picker = h("button", { class: "row picker", type: "button", onclick: () => pickProvider(providers, chosen, (p) => {
        chosen = p;
        paint();
      }) });
      const extra = h("div", { class: "stack" });
      const save = h("button", { class: "btn primary", type: "button" }, t("save"));

      const paint = () => {
        fill(picker, h("span", { class: "row-main" }, h("small", { class: "dim" }, t("provider")), h("span", {}, chosen.label)), icon("chevron-right", 18));
        fill(extra,
          chosen.needs_base_url ? base : null,
          chosen.needs_model ? model : null,
          chosen.keys_page ? h("a", { class: "link", href: chosen.keys_page, onclick: (event) => {
            event.preventDefault();
            native.open(chosen.keys_page);
          } }, t("get key"), icon("external", 14)) : null);
      };
      paint();

      save.addEventListener("click", async () => {
        message.textContent = "";
        if (!key.value.trim()) {
          message.textContent = t("key empty");
          return;
        }
        save.disabled = true;
        save.textContent = t("checking key");
        try {
          const result = await post("/api/model-key", { provider: chosen.id, key: key.value, base_url: base.value, model: model.value, headers: {}, stream: true });
          if (!result.ok) {
            message.textContent = t(`key ${result.code}`);
            return;
          }
          await refresh("key");
          native.haptic("success");
          toast(t("key saved"));
          close();
          then();
        } catch (error) {
          message.textContent = error.localized || t("pay failed");
        } finally {
          save.disabled = false;
          save.textContent = t("save");
        }
      });

      return [
        h("p", { class: "dim" }, t("key intro")),
        h("div", { class: "group" }, picker),
        h("div", { class: "field-group" }, h("label", { class: "label" }, t("key")), key),
        extra,
        message,
        save,
      ];
    },
  });
}
