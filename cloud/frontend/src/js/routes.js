// Path → screen. The pricing page is not here: it is prerendered HTML served by the server.
import { h } from "./dom.js";
import { t } from "./i18n.js";
import { emptyBox } from "./ui.js";

function placeholder(root) {
  root.append(h("h1.h2", { text: t("nav.dashboard") }), emptyBox(t("dashboard.noTasks")));
}

export const routes = [
  { pattern: /^\/dashboard\/?$/, screen: placeholder, title: "nav.dashboard", nav: "/dashboard" },
];
