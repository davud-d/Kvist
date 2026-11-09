// Provider tiles + the key field. The key is checked the moment it is pasted, with one cheap
// request through the server, and the answer is shown next to the field in plain words.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { t } from "./i18n.js";

const DEBOUNCE_MS = 700;
const DRAFT_KEY = "kvist.keyDraft";
const SIGN_IN_RESULTS = ["denied", "expired", "refused", "unreachable", "no_plan", "identity"];

/** Back from OpenAI the address says how it went (`?chatgpt=ok` or a reason); it is read once and removed. */
function takeSignInResult() {
  const url = new URL(location.href);
  const result = url.searchParams.get("chatgpt") || "";
  if (!result) return "";
  url.searchParams.delete("chatgpt");
  history.replaceState(history.state, "", url);
  return result;
}

// The provider, address and model survive a reload or a trip to another page; the key itself never does
// (it is saved the moment it is pasted, so there is nothing to lose).
function readDraft() {
  try {
    return JSON.parse(sessionStorage.getItem(DRAFT_KEY)) ?? {};
  } catch {
    // Storage blocked or the entry is not JSON: start without a draft.
    return {};
  }
}

function writeDraft(draft) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Storage blocked: the draft is only a convenience.
  }
}

export function keyWidget({ providers, saved, onValid }) {
  const returned = takeSignInResult();
  const draft = readDraft();
  const draftProvider = providers.some((item) => item.id === draft.provider) ? draft.provider : "";
  let provider = returned ? "chatgpt" : saved.set ? saved.provider : draftProvider || providers[0].id;
  let changing = !saved.set || (returned !== "" && returned !== "ok");
  let timer = null;
  let sequence = 0;
  const root = h("div.stack");

  const hint = h("p.hint", { "aria-live": "polite", id: "key-hint" });
  const keyInput = h("input.input", {
    type: "password", autocomplete: "off", spellcheck: "false", autocapitalize: "off",
    placeholder: t("onboarding.keyPlaceholder"), "aria-describedby": "key-hint", id: "model-key",
  });
  const baseInput = h("input.input", { type: "url", placeholder: t("onboarding.baseUrlPlaceholder"), id: "model-base" });
  const modelList = h("datalist", { id: "model-catalog" });
  const modelInput = h("input.input", { type: "text", spellcheck: "false", autocapitalize: "off", id: "model-name",
    list: "model-catalog", autocomplete: "off" });

  if (!saved.set && draft.provider === provider) {
    baseInput.value = draft.base_url ?? "";
    modelInput.value = draft.model ?? "";
  }
  const remember = () => writeDraft({ provider, base_url: baseInput.value.trim(), model: modelInput.value.trim() });
  baseInput.addEventListener("input", remember);
  modelInput.addEventListener("input", remember);

  const setHint = (kind, text) => {
    hint.className = `hint ${kind}`;
    hint.textContent = text;
    keyInput.setAttribute("aria-invalid", kind === "err" ? "true" : "false");
  };

  // The provider's own model list fills the suggestions; an empty Model field takes the first one.
  async function loadCatalog(key) {
    const chosen = providers.find((item) => item.id === provider) ?? {};
    if (!chosen.needs_model) return;
    const { models } = await api("/api/model-catalog", {
      method: "POST", body: { provider, key, base_url: baseInput.value.trim() },
    });
    replace(modelList, models.map((id) => h("option", { value: id })));
    if (!modelInput.value.trim() && models.length) modelInput.value = models[0];
  }

  async function check() {
    const key = keyInput.value.trim();
    if (key.length < 8) return;
    const mine = ++sequence;
    setHint("wait", t("keyCheck.checking"));
    onValid(false);
    try {
      await loadCatalog(key).catch(() => {});
      if (mine !== sequence) return;
      const result = await api("/api/model-key", {
        method: "POST",
        body: { provider, key, base_url: baseInput.value.trim(), model: modelInput.value.trim() },
      });
      if (mine !== sequence) return;
      if (!result.ok) {
        setHint("err", t(`keyCheck.${result.code}`));
        return;
      }
      saved = result.key;
      changing = false;
      keyInput.value = "";
      onValid(true);
      draw();
      root.querySelector("[data-saved]")?.focus();
    } catch (error) {
      if (mine === sequence) setHint("err", t(`errors.${error.code}`));
    }
  }

  const soon = () => { clearTimeout(timer); timer = setTimeout(check, DEBOUNCE_MS); };
  keyInput.addEventListener("input", soon);
  keyInput.addEventListener("paste", () => { clearTimeout(timer); setTimeout(check, 0); });
  const pasteButton = h("button.btn.ghost", { type: "button", text: t("onboarding.paste"), onclick: async () => {
    try {
      keyInput.value = (await navigator.clipboard.readText()).trim();
    } catch {
      // Clipboard access refused: the field still takes a normal paste.
      keyInput.focus();
      return;
    }
    clearTimeout(timer);
    check();
  } });
  baseInput.addEventListener("change", check);
  modelInput.addEventListener("change", check);

  function tiles() {
    return h("fieldset.tiles", { style: "border:0;padding:0;margin:0" },
      h("legend.sr", { text: t("onboarding.modelTitle") }),
      providers.map((item) => {
        const name = item.id === "other" ? t("onboarding.otherName") : item.label;
        const note = item.partner ? t("onboarding.partnerNote") : item.id === "other" ? t("onboarding.otherNote")
          : item.sign_in ? t("onboarding.signInNote") : "";
        return h(item.partner ? "label.tile.partner" : "label.tile", {},
          h("input", {
            type: "radio", name: "provider", value: item.id, checked: item.id === provider,
            onchange: () => {
              provider = item.id; changing = true; onValid(false); baseInput.value = ""; modelInput.value = "";
              remember(); draw();
              // The form is below a long tile list: bring it into view so the pick visibly did something.
              form()?.scrollIntoView({ behavior: "smooth", block: "start" });
              keyInput.focus({ preventScroll: true });
            },
          }),
          item.partner ? h("span.badge", { text: t("onboarding.partnerBadge") }) : null,
          h("span.name", { text: name }),
          item.keys_page
            ? h("a.where.inline", { href: item.keys_page, target: "_blank", rel: "noopener",
                text: `${t("onboarding.keysLink")} ↗` })
            : note ? h("span.where.small", { text: note }) : null);
      }));
  }

  // The plan instead of a key: the customer signs in at OpenAI and comes back here (the server keeps it good).
  function chatgptPanel() {
    const state = saved.provider === "chatgpt" ? saved.chatgpt_state : "";
    const notice = h("p.hint", { "aria-live": "polite" });
    const say = (kind, text) => { notice.className = `hint ${kind}`; notice.textContent = text; };
    if (returned && returned !== "ok") {
      say("err", t(`chatgpt.result.${SIGN_IN_RESULTS.includes(returned) ? returned : "refused"}`));
    }
    if (state === "connected") {
      return h("div.stack", {},
        h("p.row", {},
          h("span.hint.ok", { tabindex: "-1", "data-saved": "",
            text: saved.chatgpt_email ? t("chatgpt.connected", { email: saved.chatgpt_email }) : t("chatgpt.connectedNoEmail") }),
          h("button.linkbtn", { type: "button", text: t("chatgpt.disconnect"), onclick: async () => {
            await api("/api/chatgpt", { method: "DELETE" });
            saved = await api("/api/model-key");
            changing = true;
            onValid(false);
            draw();
          } })),
        notice);
    }
    const signIn = h("button.btn", { type: "button", text: t(state === "needs_login" ? "chatgpt.signInAgain" : "chatgpt.signIn") });
    signIn.addEventListener("click", async () => {
      signIn.disabled = true;
      say("wait", t("chatgpt.opening"));
      try {
        const { url } = await api("/api/chatgpt/start", { method: "POST", body: { next: location.pathname } });
        if (new URL(url).protocol !== "https:") throw Object.assign(new Error("url"), { code: "internal" });
        location.assign(url);
      } catch (error) {
        signIn.disabled = false;
        say("err", t(`errors.${error.code}`));
      }
    });
    return h("div.stack", {}, h("p.dim", { text: t(state === "needs_login" ? "chatgpt.needsLogin" : "chatgpt.intro") }),
      h("p", {}, signIn), notice);
  }

  const form = () => root.querySelector("[data-form]");

  function draw() {
    const chosen = providers.find((item) => item.id === provider) ?? {};
    const showSaved = !changing && saved.set && saved.provider === provider;
    if (chosen.sign_in) {
      replace(root, tiles(), chatgptPanel());
      return;
    }
    replace(root, tiles(),
      showSaved
        ? h("p.row", {},
            h("span.hint.ok", { tabindex: "-1", "data-saved": "", text: t("keyCheck.ok", { last4: saved.last4 }) }),
            h("button.linkbtn", { type: "button", text: t("onboarding.changeKey"),
              onclick: () => { changing = true; onValid(false); draw(); keyInput.focus(); } }))
        : h("div.stack", { "data-form": "" },
            chosen.needs_base_url
              ? h("label.field", { for: "model-base" }, h("span", { text: t("onboarding.baseUrl") }), baseInput) : null,
            chosen.needs_model
              ? h("label.field", { for: "model-name" }, h("span", { text: t("onboarding.modelName") }), modelInput, modelList) : null,
            h("label.field", { for: "model-key" }, h("span", { text: t("onboarding.keyLabel") }),
              h("span.keyrow", {}, keyInput, pasteButton)),
            hint));
  }

  draw();
  onValid(!changing && saved.set);
  return root;
}
