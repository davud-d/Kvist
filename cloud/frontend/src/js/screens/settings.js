// Settings: the plan with a visible one-click cancel, the model key, language, account, machine.
import { api } from "../api.js";
import { startCheckout } from "../billing.js";
import { h, modal, replace, skeleton } from "../dom.js";
import { shortDate } from "../format.js";
import { locale, rememberLocale, setLocale, t } from "../i18n.js";
import { keyWidget } from "../keywidget.js";
import { errorBox } from "../ui.js";

const PRICES = { start: 15, pro: 39, max: 99 };

function planText(view) {
  const date = shortDate(view.cancel_at_period_end ? view.period_end : view.trial_ends_at || view.period_end, locale());
  if (view.cancel_at_period_end || view.status === "cancelled") return t("settings.cancelledUntil", { date });
  if (view.status === "trialing") return t("settings.trialUntil", { date, price: view.price_after_trial_usd });
  return t("settings.activeUntil", { plan: t(`pricing.planNames.${view.plan}`), price: PRICES[view.plan] || "", date });
}

export async function settingsScreen(root, { me, navigate }) {
  replace(root, skeleton(6));
  const [view, providers, saved, machines] = await Promise.all([
    api("/api/billing/subscription"), api("/api/model-providers"), api("/api/model-key"), api("/api/machines"),
  ]);
  const planBox = h("div.stack");
  const drawPlan = (current) => {
    const cancel = h("button.btn.danger", { type: "button", text: t("settings.cancel") });
    cancel.addEventListener("click", () => modal((close) => h("div.stack", {},
      h("h2.h3", {}, t("settings.cancelTitle")),
      h("p.dim", {}, t("settings.cancelBody", { date: shortDate(current.trial_ends_at || current.period_end, locale()) })),
      h("div.row", {},
        h("button.btn.danger", { type: "button", text: t("settings.cancelYes"), onclick: async () => {
          close();
          try {
            drawPlan(await api("/api/billing/cancel", { method: "POST" }));
          } catch (error) {
            planBox.append(errorBox(error));
          }
        } }),
        h("button.btn.ghost", { type: "button", autofocus: true, onclick: close, text: t("settings.cancelKeep") })))));
    // Plans do not renew by themselves: near the end the person pays again, and the new month follows this one.
    const renew = h("button.btn", { type: "button", text: t("settings.renew") });
    renew.addEventListener("click", async () => {
      renew.disabled = true;
      try {
        await startCheckout(current.plan);
      } catch (error) {
        renew.disabled = false;
        planBox.append(errorBox(error));
      }
    });
    replace(planBox,
      current.can_renew ? h("p", {}, renew) : null,
      current.status === "none"
        ? h("p.dim", {}, t("dashboard.noPlan"))
        : h("p", {}, planText(current)),
      current.status !== "none" && !current.cancel_at_period_end && current.status !== "cancelled" ? h("p", {}, cancel) : null);
  };
  drawPlan(view);

  const keyBox = h("div.stack", {}, keyWidget({ providers, saved, onValid: () => {} }),
    saved.set ? h("p", {}, h("button.linkbtn", { type: "button", text: t("settings.removeKey"), onclick: async () => {
      await api("/api/model-key", { method: "DELETE" });
      navigate("/settings", { replace: true });
    } })) : null);

  const language = h("div.row", { role: "group", "aria-label": t("settings.langTitle") },
    [["en", "English"], ["ru", "Русский"]].map(([code, name]) => h("button.chip", {
      type: "button", "aria-pressed": String(locale() === code), lang: code, text: name,
      onclick: async () => {
        setLocale(code);
        rememberLocale(code);
        await api("/api/me", { method: "PATCH", body: { locale: code } }).catch(() => {});
        navigate("/settings", { replace: true });
      },
    })));

  const machine = machines[0];
  const machineBox = machine
    ? h("p", {}, h("button.btn.danger", { type: "button", text: t("settings.deleteMachine"), onclick: () => modal((close) =>
        h("div.stack", {},
          h("h2.h3", {}, t("settings.deleteTitle")),
          h("p.dim", {}, t("settings.deleteBody")),
          h("div.row", {},
            h("button.btn.danger", { type: "button", text: t("settings.deleteYes"), onclick: async () => {
              close();
              try {
                await api(`/api/machines/${machine.id}`, { method: "DELETE" });
                navigate("/dashboard");
              } catch (error) {
                machineBox.append(errorBox(error));
              }
            } }),
            h("button.btn.ghost", { type: "button", autofocus: true, onclick: close, text: t("common.cancel") })))) }))
    : h("p.dim", {}, t("dashboard.noMachine"));

  replace(root, h("div.stack-lg", {},
    h("h1.h2", {}, t("settings.title")),
    h("section.card", {}, h("h2.h3", {}, t("settings.planTitle")), planBox),
    h("section.card", {}, h("h2.h3", {}, t("settings.keyTitle")), keyBox),
    h("section.card", {}, h("h2.h3", {}, t("settings.langTitle")), language),
    h("section.card", {}, h("h2.h3", {}, t("settings.accountTitle")),
      h("p.dim", {}, t("settings.signedInAs", { login: me ? me.login : "" })),
      h("p", {}, h("button.btn.ghost", { type: "button", text: t("common.signOut"), onclick: async () => {
        await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
        location.href = "/";
      } }))),
    h("section.card", {}, h("h2.h3", {}, t("settings.machineTitle")), machineBox)));
  return null;
}
