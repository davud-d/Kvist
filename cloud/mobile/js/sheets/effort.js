// How hard the model thinks: three levels the cloud offers a phone. It applies from the next start of the agent.

import { account, refresh } from "../account.js";
import { api } from "../api.js";
import { h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { openSheet, toast } from "../sheet.js";

export const LEVELS = ["low", "medium", "high"];

export const effortOf = () => account.key?.effort || "medium";

export function effortSheet() {
  openSheet({
    title: t("effort"),
    build: (close) => h("div", { class: "choices" }, LEVELS.map((level) =>
      h("button", { class: `choice${effortOf() === level ? " on" : ""}`, type: "button", onclick: async () => {
        try {
          await api("/api/effort", { method: "PUT", body: { level } });
          await refresh("key");
          close();
        } catch (error) {
          toast(error.localized, "error");
        }
      } }, h("span", {}, t(`effort ${level}`)), effortOf() === level ? icon("check", 18) : null))),
  });
}
