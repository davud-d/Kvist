// The side sheet: details that matter now and then, on request, never as a standing column. The server and the
// hours (from the rail), the agent's screen, its helpers and background commands (from the task). It sits over the edge of the stage and leaves the work visible; the same button, the cross or Esc
// closes it. Its content is the old inspector's panels, unchanged.

import { onAccount } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { animate, finished } from "../motion.js";
import { helpers } from "../panels/helpers.js";
import { hours } from "../panels/hours.js";
import { jobs } from "../panels/jobs.js";
import { screen } from "../panels/screen.js";
import { server } from "../panels/server.js";
import { active, setSheet, store, subscribe } from "../store.js";

function serverAndHours(el) {
  const top = h("div", { class: "sheet-part" });
  const bottom = h("div", { class: "sheet-part" });
  server.render(active(), top);
  hours.render(active(), bottom);
  fill(el, top, bottom);
}

const KINDS = {
  server: { title: () => t("server and hours"), render: serverAndHours, account: true },
  screen: { title: () => t("screen"), render: (el) => screen.render(active(), el) },
  helpers: { title: () => t("helpers"), render: (el) => helpers.render(active(), el) },
  jobs: { title: () => t("jobs"), render: (el) => jobs.render(active(), el) },
};

const kindOf = (sheet) => KINDS[sheet.kind.split(":")[0]];

export function mountSheet(root) {
  const title = h("h2", { class: "sheet-title" });
  const close = h("button", { class: "btn icon-only", type: "button", title: t("close"), "aria-label": t("close"), onclick: () => setSheet(null) }, icon("close", 18));
  const body = h("div", { class: "sheet-body" });
  root.append(h("div", { class: "sheet-head" }, title, close), body);
  root.setAttribute("role", "complementary");
  let shown = null;

  const redraw = frameThrottle(() => {
    const sheet = store.sheet;
    // A task's sheet has nothing to show without the task.
    const kind = sheet && kindOf(sheet);
    if (!sheet || !kind || (!kind.account && !active())) {
      if (shown) hide();
      return;
    }
    if (!shown) show();
    shown = sheet;
    title.textContent = kind.title(sheet);
    kind.render(body, sheet);
  });

  function show() {
    root.hidden = false;
    document.getElementById("app").classList.add("has-sheet");
    animate(root, [{ transform: "translateX(24px)", opacity: 0 }, { transform: "none", opacity: 1 }], { curve: "drawer", ms: 260 });
  }

  async function hide() {
    shown = null;
    document.getElementById("app").classList.remove("has-sheet");
    await finished(animate(root, [{ transform: "none", opacity: 1 }, { transform: "translateX(16px)", opacity: 0 }], { ms: 160, fill: "forwards" }));
    if (!store.sheet) root.hidden = true;
    root.getAnimations().forEach((animation) => animation.cancel());
  }

  // Captured, so Esc closes the sheet before it reaches the task (where it would pause the agent).
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && store.sheet && !document.querySelector("#modals > *")) {
      setSheet(null);
      event.stopPropagation();
    }
  }, true);
  subscribe(redraw);
  onAccount(redraw);
}
