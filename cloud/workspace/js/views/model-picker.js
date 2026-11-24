// The provider and model the next task runs on, picked inside the home field. A provider is a parent row with its
// mark; opening it shows what is under it: the models its saved key can use (or a field to type one), or, for a
// provider without a key yet, the way to connect one. A search at the top looks through providers and models.
//
// One key is saved per account (the cloud keeps it encrypted and never hands it back), so only the connected
// provider lists models; DeepSeek, OpenAI and Anthropic have their models chosen by Kvist itself.

import { account, api, refreshKey } from "../cloud.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { animate } from "../motion.js";
import { modelName } from "../models.js";
import { PROVIDER_MARKS } from "../provider-icons.js";
import { openKeyDialog } from "./settings-dialogs.js";
import { toast } from "./toast.js";

/** A provider's mark: its logo when there is one, else its first letter, on the same neutral tile. */
export function providerMark(id, label = "") {
  const tile = h("span", { class: "provider-mark", "aria-hidden": "true" });
  const path = PROVIDER_MARKS[id];
  if (path) tile.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="${path}"/></svg>`;
  else if (id === "anymodel") tile.append(icon("twig", 14));
  // The person's own endpoint has no brand: it is an address on the web.
  else if (id === "other") tile.append(icon("globe", 14));
  else tile.textContent = (label || id).replace(/^[^A-Za-zА-Яа-я0-9]+/, "").slice(0, 1).toUpperCase();
  return tile;
}

// The search finds a model by its id or by the name the page shows for it.
const matches = (id, query) => !query || id.toLowerCase().includes(query) || modelName(id).toLowerCase().includes(query);

const labelOf = (provider) => (provider.id === "other" ? t("other") : provider.label);

/** What the field's button says: the model when the person picked one, else the provider. */
export function pickedLabel() {
  const key = account.key;
  if (!key?.set) return t("connect model");
  return key.model ? modelName(key.model) : key.label || key.provider;
}

// The saved key's models, asked once per page (and again after the key changes).
const catalog = { forKey: "", models: null, loading: false };

async function loadModels(redraw) {
  const stamp = `${account.key?.provider}:${account.key?.last4}:${account.key?.base_url}`;
  if (catalog.forKey === stamp && (catalog.models || catalog.loading)) return;
  Object.assign(catalog, { forKey: stamp, models: null, loading: true });
  try {
    catalog.models = (await api("/api/model-catalog/saved")).models ?? [];
  } catch (error) {
    catalog.models = [];
    toast(error.localized ?? String(error), "error");
  }
  catalog.loading = false;
  redraw();
}

async function switchModel(model, close) {
  const name = model.trim();
  if (!name || name === account.key?.model) return close();
  // A refused model is said in words (a check code, or the server's own message) and nothing changes.
  const reply = await api("/api/model", { method: "PUT", body: { model: name } }).catch((error) => ({ ok: false, text: error.localized }));
  if (!reply.ok) {
    toast(reply.text ?? t(reply.code), "error");
    return undefined;
  }
  await refreshKey();
  toast(`${t("model chosen")}: ${name}`);
  return close();
}

export function openModelMenu(host, close) {
  const search = h("input", { class: "model-search", type: "search", placeholder: t("search models"), spellcheck: "false", "aria-label": t("search models") });
  const list = h("div", { class: "model-tree", role: "tree" });
  const menu = h("div", { class: "menu model-menu", role: "dialog", "aria-label": t("model") }, h("div", { class: "model-search-row" }, icon("search", 16), search), list);
  let open = account.key?.set ? account.key.provider : "";
  // The provider the person just opened: its children unroll from under it once.
  let unroll = "";

  const childrenOf = (provider, query) => {
    const key = account.key;
    if (key?.set && key.provider === provider.id) {
      if (provider.sign_in) return [h("p", { class: "model-note" }, t("chatgpt models note"))];
      if (!provider.needs_model) return [h("p", { class: "model-note" }, t("kvist picks models"))];
      loadModels(paint);
      const models = (catalog.models ?? []).filter((id) => matches(id, query));
      const manual = h("input", { class: "field model-manual", type: "text", placeholder: t("type model"), spellcheck: "false", value: query && !models.includes(query) ? query : "" });
      manual.addEventListener("keydown", (event) => event.key === "Enter" && switchModel(manual.value, close));
      return [
        catalog.loading ? h("p", { class: "model-note" }, t("loading")) : null,
        ...models.map((id) => h("button", { class: `model-row${id === key.model ? " on" : ""}`, type: "button", role: "treeitem", onclick: () => switchModel(id, close) }, h("span", { class: "model-names" }, h("span", {}, modelName(id)), h("span", { class: "model-id" }, id)), id === key.model ? icon("check", 16) : null)),
        !catalog.loading && !models.length && !query ? h("p", { class: "model-note" }, t("no model list")) : null,
        manual,
      ];
    }
    return [
      h("p", { class: "model-note" }, t("needs key").replace("{name}", labelOf(provider))),
      h("button", { class: "btn", type: "button", onclick: () => { close(); openKeyDialog(provider.id); } }, t("connect key")),
    ];
  };

  function paint() {
    const query = search.value.trim().toLowerCase();
    // The connected provider first: its models are what the person switches between most.
    const connectedFirst = [...account.providers].sort((a, b) => Number(b.id === account.key?.provider) - Number(a.id === account.key?.provider));
    const providers = connectedFirst.filter((provider) => {
      if (!query) return true;
      if (labelOf(provider).toLowerCase().includes(query)) return true;
      // The connected provider stays while its models match the search.
      return account.key?.provider === provider.id && (catalog.models ?? []).some((id) => matches(id, query));
    });
    fill(list, providers.length ? providers.map((provider) => {
      const connected = account.key?.set && account.key.provider === provider.id;
      const expanded = open === provider.id || (query && connected);
      const head = h("button", { class: `provider-row${connected ? " connected" : ""}`, type: "button", role: "treeitem", "aria-expanded": String(Boolean(expanded)), onclick: () => { open = open === provider.id ? "" : provider.id; unroll = open; paint(); } },
        providerMark(provider.id, provider.label),
        h("span", { class: "provider-name" }, labelOf(provider)),
        connected ? h("span", { class: "provider-state" }, t("connected")) : null,
        icon("chev", 14));
      const children = h("div", { class: "provider-children", role: "group" }, expanded ? childrenOf(provider, query) : null);
      if (expanded && unroll === provider.id) {
        unroll = "";
        animate(children, [{ clipPath: "inset(0 0 100% 0)", opacity: 0, transform: "translateY(-4px)" }, { clipPath: "inset(0 0 0 0)", opacity: 1, transform: "none" }], { curve: "drawer", ms: 240 });
      }
      return h("div", { class: `provider${expanded ? " open" : ""}` }, head, children);
    }) : h("p", { class: "model-note" }, t("nothing found")));
  }

  search.addEventListener("input", paint);
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  });
  host.append(menu);
  paint();
  queueMicrotask(() => search.focus());
  return menu;
}
