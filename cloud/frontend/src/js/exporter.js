// "Export archive" / "Download archive": ask, wait while the machine packs the files, then a link.
import { api } from "./api.js";
import { h, replace } from "./dom.js";
import { t } from "./i18n.js";
import { errorBox } from "./ui.js";

const POLL_MS = 1500;

export function exportButton(path, label) {
  const slot = h("div.stack");
  const start = async () => {
    replace(slot, h("p.row.small", { "aria-live": "polite" },
      h("span.pill.working", {}, h("span.led", { "aria-hidden": "true" })), t("task.preparingArchive")));
    try {
      let job = await api(path, { method: "POST" });
      while (job.status === "pending") {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        job = await api(`/api/exports/${job.id}`);
      }
      if (job.status !== "ready") {
        replace(slot, h("p.hint.err", {}, t("task.archiveFailed")), button());
        return;
      }
      replace(slot, h("a.btn", { href: job.download_url, download: "" }, t("dashboard.exportReady")));
    } catch (error) {
      replace(slot, errorBox(error), button());
    }
  };
  const button = () => h("button.btn.ghost", { type: "button", onclick: start }, label);
  replace(slot, button());
  return slot;
}
