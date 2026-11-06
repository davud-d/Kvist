// "Reset the machine": a clean one in place of this, for a machine that broke, hung or is gone.
// One dialog for the settings page and for the dashboard when the machine is in trouble.
import { api } from "./api.js";
import { h, modal, replace } from "./dom.js";
import { t } from "./i18n.js";
import { errorBox } from "./ui.js";

export function resetMachineDialog(machine, onDone) {
  modal((close) => {
    const problem = h("div", { "aria-live": "polite" });
    const yes = h("button.btn.danger", { type: "button", text: t("machine.resetYes") });
    yes.addEventListener("click", async () => {
      yes.disabled = true;
      replace(problem);
      try {
        await api(`/api/machines/${machine.id}/reset`, { method: "POST" });
        close();
        onDone();
      } catch (error) {
        yes.disabled = false;
        replace(problem, errorBox(error));
      }
    });
    return h("div.stack", {},
      h("h2.h3", {}, t("machine.resetTitle")),
      h("p.dim", {}, t("machine.resetBody")),
      problem,
      h("div.row", {}, yes,
        h("button.btn.ghost", { type: "button", autofocus: true, onclick: close, text: t("common.cancel") })));
  });
}
