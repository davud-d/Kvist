// The provider and model, picked in a menu that stands above the model chip, as in the web workspace: a search, the providers
// with their marks (the connected one first, open), and under it the models its saved key can use, or a field to type one. A
// provider without a key sends the person to connect one. Nothing slides in from the edge: it grows out of the chip.

import { account, refresh } from "./account.js";
import { api } from "./api.js";
import { fill, h, icon } from "./dom.js";
import { t } from "./i18n.js";
import { providerMark } from "./mark.js";
import { openPopover } from "./popover.js";
import { toast } from "./sheet.js";
import { keySheet } from "./sheets/key.js";
import { modelName } from "./web/models.js";

const labelOf = (provider) => (provider.id === "other" ? t("other") : provider.label);
const matches = (id, query) => !query || id.toLowerCase().includes(query) || modelName(id).toLowerCase().includes(query);

/** What the chip says: the model the person picked, else the provider, else an invitation to connect one. */
export function pickedLabel() {
  const key = account.key;
  if (!key?.set) return t("connect model");
  return key.model ? modelName(key.model) : key.label || key.provider;
}

// The saved key's models, asked once for a key (and again after the key changes).
const catalog = { forKey: "", models: null, loading: false };

async function loadModels(redraw) {
  const stamp = `${account.key?.provider}:${account.key?.last4}:${account.key?.base_url}`;
  if (catalog.forKey === stamp && (catalog.models || catalog.loading)) return;
  Object.assign(catalog, { forKey: stamp, models: null, loading: true });
  try {
    catalog.models = (await api("/api/model-catalog/saved")).models ?? [];
  } catch (error) {
    catalog.models = [];
    toast(error.localized, "error");
  }
  catalog.loading = false;
  redraw();
}

async function switchModel(model, close) {
  const name = model.trim();
  if (!name || name === account.key?.model) return close();
  // A refused model is said in words and nothing changes.
  const reply = await api("/api/model", { method: "PUT", body: { model: name } }).catch((error) => ({ ok: false, text: error.localized }));
  if (!reply.ok) {
    toast(reply.text ?? t(`key ${reply.code}`), "error");
    return undefined;
  }
  await refresh("key");
  return close();
}

export function openModelMenu(anchor) {
  const search = h("input", { class: "model-search", type: "search", placeholder: t("search models"), spellcheck: "false", autocapitalize: "off", "aria-label": t("search models") });
  const list = h("div", { class: "model-tree", role: "tree" });
  const card = h("div", { class: "model-menu" }, h("div", { class: "model-search-row" }, icon("search", 16), search), list);
  let open = account.key?.set ? account.key.provider : "";
  let close = () => {};

  const childrenOf = (provider, query) => {
    const key = account.key;
    if (key?.set && key.provider === provider.id) {
      if (provider.sign_in) return [h("p", { class: "model-note" }, t("chatgpt models note"))];
      if (!provider.needs_model) return [h("p", { class: "model-note" }, t("kvist picks models"))];
      loadModels(paint);
      const models = (catalog.models ?? []).filter((id) => matches(id, query));
      const manual = h("input", { class: "field model-manual", type: "text", placeholder: t("type model"), spellcheck: "false", autocapitalize: "off", enterkeyhint: "done", value: query && !models.includes(query) ? query : "" });
      manual.addEventListener("keydown", (event) => event.key === "Enter" && switchModel(manual.value, close));
      return [
        catalog.loading ? h("p", { class: "model-note" }, t("loading")) : null,
        ...models.map((id) => h("button", { class: `model-row${id === key.model ? " on" : ""}`, type: "button", role: "treeitem", onclick: () => switchModel(id, close) },
          h("span", { class: "model-names" }, h("span", {}, modelName(id)), modelName(id) !== id ? h("small", {}, id) : null),
          id === key.model ? icon("check", 16) : null)),
        !catalog.loading && !models.length && !query ? h("p", { class: "model-note" }, t("no model list")) : null,
        manual,
      ];
    }
    return [
      h("p", { class: "model-note" }, t("needs key", { name: labelOf(provider) })),
      h("button", { class: "btn", type: "button", onclick: () => {
        close();
        keySheet(() => {}, provider.id);
      } }, t("connect key")),
    ];
  };

  function paint() {
    const query = search.value.trim().toLowerCase();
    // The connected provider first: its models are what the person switches between most.
    const providers = [...account.providers.filter((p) => !p.sign_in || p.id === account.key?.provider)]
      .sort((a, b) => Number(b.id === account.key?.provider) - Number(a.id === account.key?.provider))
      .filter((provider) => !query || labelOf(provider).toLowerCase().includes(query)
        || (account.key?.provider === provider.id && (catalog.models ?? []).some((id) => matches(id, query))));
    fill(list, providers.length ? providers.map((provider) => {
      const connected = account.key?.set && account.key.provider === provider.id;
      const expanded = open === provider.id || (query && connected);
      return h("div", { class: `provider${expanded ? " open" : ""}` },
        h("button", { class: `provider-row${connected ? " connected" : ""}`, type: "button", role: "treeitem", aria: { expanded: String(Boolean(expanded)) }, onclick: () => {
          open = open === provider.id ? "" : provider.id;
          paint();
        } }, providerMark(provider.id, provider.label, 16), h("span", { class: "provider-name" }, labelOf(provider)),
        connected ? h("span", { class: "provider-state" }, t("connected")) : null,
        h("span", { class: `chev${expanded ? " up" : ""}` }, icon("chevron-down", 14))),
        h("div", { class: "provider-children", role: "group" }, expanded ? childrenOf(provider, query) : null));
    }) : h("p", { class: "model-note" }, t("nothing found")));
  }
  search.addEventListener("input", paint);
  paint();
  close = openPopover(anchor, [], { content: card, wide: true });
  return close;
}
