// What the next run goes on, inside a message field: the provider and model, and how hard the model thinks. Both
// belong to the account (one key, one effort), so the home field and a task's field show the same two buttons and
// change the same thing; a task takes them from its next message on. One menu at a time per field; a click
// elsewhere or Esc closes it.

import { effortNow } from "../actions.js";
import { account } from "../cloud.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { popIn } from "../motion.js";
import { effortDial, effortWord } from "./effort.js";
import { openModelMenu, pickedLabel, providerMark } from "./model-picker.js";

/** The one-menu-at-a-time rule of a field: `toggle(button, build)` opens what `build` returns under that button. */
export function fieldMenus(input, origin = "0 0") {
  let menu = null;
  let owner = null;
  const outside = (event) => {
    if (menu && !menu.contains(event.target) && !owner.contains(event.target)) close();
  };
  // Esc closes the menu and nothing else: in a task it would otherwise pause the agent.
  const escape = (event) => {
    if (event.key !== "Escape" || event.shiftKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    close();
  };
  function close() {
    menu?.remove();
    menu = null;
    owner = null;
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", escape, true);
    input.focus();
  }
  function toggle(button, build) {
    const same = owner === button;
    if (menu) close();
    if (same) return;
    owner = button;
    menu = build();
    popIn(menu, origin);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape, true);
  }
  return { toggle, close, isOpen: (button) => owner === button };
}

/** The model and effort buttons for `composer`; `redraw()` shows what the account has now. */
export function runPicks(composer, menus) {
  const model = h("button", { class: "project-pick model-pick", type: "button", "aria-haspopup": "dialog" });
  const effort = h("button", { class: "project-pick effort-pick", type: "button", "aria-haspopup": "dialog", title: t("effort") });
  const openModel = () => menus.toggle(model, () => openModelMenu(composer, menus.close));
  model.addEventListener("click", openModel);
  // The effort dial names the model it applies to; that line opens the model menu in its place.
  effort.addEventListener("click", () => menus.toggle(effort, () => {
    const built = h("div", { class: "menu effort-menu", role: "dialog", "aria-label": t("effort") }, effortDial({ onModel: openModel }));
    built.style.left = `${effort.offsetLeft}px`;
    composer.append(built);
    queueMicrotask(() => built.querySelector(".effort-track").focus());
    return built;
  }));
  const redraw = () => {
    model.title = account.key?.model || t("model");
    fill(model, account.key?.set ? providerMark(account.key.provider, account.key.label) : icon("plus", 16), h("span", {}, pickedLabel()), icon("chev", 14));
    fill(effort, icon("gauge", 16), h("span", {}, effortWord(effortNow())), icon("chev", 14));
  };
  redraw();
  return { model, effort, redraw, openModel: () => !menus.isOpen(model) && openModel() };
}
