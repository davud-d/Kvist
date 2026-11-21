// Shown once to a new account: both boxes (terms of service, privacy policy) before anything else.
import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { t } from "../i18n.js";
import { errorBox } from "../ui.js";

function consent(key, linkKey, href, box) {
  const [before, after = ""] = t(key, { link: "\u0000" }).split("\u0000");
  return h("label.consent", {}, box,
    h("span", {}, before, h("a", { href, target: "_blank", rel: "noopener", text: t(linkKey) }), after));
}

export function termsScreen(root, onAccepted) {
  const terms = h("input", { type: "checkbox", id: "accept-terms" });
  const privacy = h("input", { type: "checkbox", id: "accept-privacy" });
  const problem = h("div");
  const submit = h("button.btn.wide", { type: "button", text: t("terms.continue"), disabled: true });
  const sync = () => { submit.disabled = !(terms.checked && privacy.checked); };
  terms.addEventListener("change", sync);
  privacy.addEventListener("change", sync);
  submit.addEventListener("click", async () => {
    submit.disabled = true;
    try {
      await api("/api/me/terms", { method: "POST", body: { terms: terms.checked, privacy: privacy.checked } });
      await onAccepted();
    } catch (error) {
      replace(problem, errorBox(error));
      sync();
    }
  });
  replace(root,
    h("section.stack", {},
      h("h1.h2", { text: t("terms.title") }),
      h("p", { text: t("terms.lead") }),
      consent("terms.acceptTerms", "terms.termsLink", "/terms", terms),
      consent("terms.acceptPrivacy", "terms.privacyLink", "/privacy", privacy),
      h("p.small", { text: t("terms.docsRu") }),
      submit,
      problem));
}
