// Admin: funnel conversion between steps, and open disputes with a refund.
import { api } from "../api.js";
import { h, replace, skeleton } from "../dom.js";
import { hoursMinutes } from "../format.js";
import { t } from "../i18n.js";
import { emptyBox, errorBox } from "../ui.js";

function percent(share) {
  return share === null ? "—" : `${(share * 100).toFixed(1)}%`;
}

function disputeRow(item, reload) {
  const minutes = h("input.input", { type: "number", min: 1, value: Math.max(1, Math.ceil(item.charged_seconds / 60)),
    "aria-label": t("admin.refundMinutes"), style: "max-width:8em" });
  const note = h("input.input", { type: "text", value: "", placeholder: "note", "aria-label": "note" });
  const status = h("div");
  const send = (path, body) => async () => {
    try {
      await api(`/api/admin/disputes/${item.id}/${path}`, { method: "POST", body });
      reload();
    } catch (error) {
      replace(status, errorBox(error));
    }
  };
  return h("li.stack", {},
    h("p", {}, h("b", {}, `#${item.id} ${item.user}`), ` · ${hoursMinutes(item.charged_seconds, t)}`,
      item.task_id ? [" · ", h("span.small", {}, `task ${item.task_id}`)] : null),
    h("p.dim", {}, item.message),
    h("div.row", {}, minutes, note,
      h("button.btn", { type: "button", text: t("admin.refund"),
        onclick: () => send("refund", { minutes: Number(minutes.value), note: note.value || "refund" })() }),
      h("button.btn.ghost", { type: "button", text: t("admin.reject"), onclick: send("close") })),
    status);
}

export async function adminScreen(root) {
  const load = async () => {
    const [funnel, disputes] = await Promise.all([api("/api/admin/funnel"), api("/api/admin/disputes")]);
    replace(root, h("div.stack-lg", {},
      h("h1.h2", {}, t("admin.title")),
      h("section.card", {}, h("h2.h3", {}, t("admin.funnel")),
        h("div.table-scroll", {}, h("table.plans", {},
          h("thead", {}, h("tr", {}, [t("admin.step"), t("admin.people"), t("admin.fromPrevious")]
            .map((label) => h("th", { scope: "col" }, label)))),
          h("tbody", {}, funnel.steps.map((row) => h("tr", {},
            h("th", { scope: "row" }, row.step), h("td", {}, String(row.people)), h("td", {}, percent(row.from_previous)))))))),
      h("section.card", {}, h("h2.h3", {}, t("admin.disputes")),
        disputes.length ? h("ul.list", {}, disputes.map((item) => disputeRow(item, reload))) : emptyBox(t("admin.noDisputes")))));
  };
  const reload = () => load().catch((error) => replace(root, errorBox(error, reload)));
  replace(root, skeleton(6));
  await load();
  return null;
}
