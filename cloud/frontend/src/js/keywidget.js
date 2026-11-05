// Provider tiles + the key field. The key is checked the moment it is pasted, with one cheap
// request through the server, and the answer is shown next to the field in plain words.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { t } from "./i18n.js";

const DEBOUNCE_MS = 700;

export function keyWidget({ providers, saved, onValid }) {
  let provider = saved.set ? saved.provider : "deepseek";
  let changing = !saved.set;
  let timer = null;
  let sequence = 0;
  const root = h("div.stack");

  const hint = h("p.hint", { "aria-live": "polite", id: "key-hint" });
  const keyInput = h("input.input", {
    type: "password", autocomplete: "off", spellcheck: "false", autocapitalize: "off",
    placeholder: t("onboarding.keyPlaceholder"), "aria-describedby": "key-hint", id: "model-key",
  });
  const baseInput = h("input.input", { type: "url", placeholder: t("onboarding.baseUrlPlaceholder"), id: "model-base" });
  const modelInput = h("input.input", { type: "text", spellcheck: "false", autocapitalize: "off", id: "model-name" });

  const setHint = (kind, text) => {
    hint.className = `hint ${kind}`;
    hint.textContent = text;
    keyInput.setAttribute("aria-invalid", kind === "err" ? "true" : "false");
  };

  async function check() {
    const key = keyInput.value.trim();
    if (key.length < 8) return;
    const mine = ++sequence;
    setHint("wait", t("keyCheck.checking"));
    onValid(false);
    try {
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
  baseInput.addEventListener("change", check);
  modelInput.addEventListener("change", check);

  function tiles() {
    return h("fieldset.tiles", { style: "border:0;padding:0;margin:0" },
      h("legend.sr", { text: t("onboarding.modelTitle") }),
      providers.map((item) => {
        const name = item.id === "other" ? t("onboarding.otherName") : item.label;
        return h("label.tile", {},
          h("input", {
            type: "radio", name: "provider", value: item.id, checked: item.id === provider,
            onchange: () => { provider = item.id; changing = true; onValid(false); draw(); keyInput.focus(); },
          }),
          h("span.name", { text: name }),
          item.keys_page
            ? h("a.where.inline", { href: item.keys_page, target: "_blank", rel: "noopener",
                text: `${t("onboarding.keysLink")} ↗` })
            : h("span.where.small", { text: t("onboarding.otherNote") }));
      }));
  }

  function draw() {
    const showSaved = !changing && saved.set && saved.provider === provider;
    replace(root, tiles(),
      showSaved
        ? h("p.row", {},
            h("span.hint.ok", { tabindex: "-1", "data-saved": "", text: t("keyCheck.ok", { last4: saved.last4 }) }),
            h("button.linkbtn", { type: "button", text: t("onboarding.changeKey"),
              onclick: () => { changing = true; onValid(false); draw(); keyInput.focus(); } }))
        : h("div.stack", {},
            provider === "other"
              ? [h("label.field", { for: "model-base" }, h("span", { text: t("onboarding.baseUrl") }), baseInput),
                 h("label.field", { for: "model-name" }, h("span", { text: t("onboarding.modelName") }), modelInput)]
              : null,
            h("label.field", { for: "model-key" }, h("span", { text: t("onboarding.keyLabel") }), keyInput),
            hint));
  }

  draw();
  onValid(!changing && saved.set);
  return root;
}
