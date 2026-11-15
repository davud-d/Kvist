// An executor's journal, to read: its steps and results drawn by the same item views as the conversation, with no input.

import { w } from "../bestof-words.js";
import { fill, h, keyedList } from "../dom.js";
import { makeItem, paintItem } from "./items.js";

/** Mounts the log into `el`; `paint(log)` redraws it. */
export function mountExecLog(el, { onBack, onRefresh }) {
  const back = h("button", { class: "btn", type: "button", onclick: onBack }, h("span", {}, w("back")));
  const title = h("div", { class: "bo-title" });
  const readonly = h("span", { class: "chip" });
  const refresh = h("button", { class: "btn flat", type: "button", onclick: onRefresh });
  const note = h("div", { class: "bo-log-note dim" });
  const body = h("div", { class: "feed bo-log-body", tabindex: "-1" });
  el.append(h("div", { class: "bo-head" }, back, h("div", { class: "bo-heading" }, title), readonly, refresh), note, body);
  let shown = null;
  let draw = null;

  return (log) => {
    back.lastChild.textContent = `← ${w("back")}`;
    title.textContent = log.style || log.id;
    readonly.textContent = w("logReadonly");
    refresh.textContent = w("refresh");
    if (shown !== log) {
      fill(body);
      draw = keyedList(body, { key: (item) => item.id, make: makeItem, update: paintItem });
      shown = log;
    }
    const items = log.view.items;
    const words = { loading: w("logLoading"), failed: log.error ? `${w("logFailed")}: ${log.error}` : w("logFailed"), ready: items.length ? (log.older ? w("logOlder") : "") : w("logEmpty") };
    note.textContent = words[log.status];
    draw(items);
  };
}
