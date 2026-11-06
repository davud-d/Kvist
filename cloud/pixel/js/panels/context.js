// The context window and the files the agent changed.

import { basename, fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";

export const context = {
  id: "context",
  title: "context",
  icon: "ctx",
  badge: (agent) => agent.changed.size || "",
  render(agent, el) {
    const { tokens, limit } = agent.context;
    const share = limit ? Math.min(1, tokens / limit) : 0;
    const files = [...agent.changed].reverse();
    fill(el, 
      h("div", { class: "ctx-card px" }, h("div", { class: "card-title" }, t("context left").toUpperCase()), h("div", { class: "meter-bar big" }, ...Array.from({ length: 24 }, (_, i) => h("i", { class: i < Math.round(share * 24) ? "on" : "" }))), h("div", { class: "dim" }, limit ? `${tokens.toLocaleString()} / ${limit.toLocaleString()} ${t("tokens")}` : "—"), h("div", { class: "dim" }, `↑ ${agent.spend.input.toLocaleString()}  ↓ ${agent.spend.output.toLocaleString()}`)),
      h("div", { class: "card-title pad" }, t("files changed").toUpperCase()),
      ...(files.length
        ? files.map(([path, c]) => h("div", { class: "file-row", title: path }, icon("file", 12), h("span", { class: "file-name" }, basename(path)), h("b", { class: "plus" }, `+${c.added}`), h("b", { class: "minus" }, `−${c.removed}`)))
        : [h("p", { class: "dim pad" }, t("no changes"))]),
    );
  },
};
