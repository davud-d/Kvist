// The start of a new session: one short question. The project, the model and the effort are chips under the field; what
// is missing (a plan, a model key) is said once, above it, where the person is about to type.

import { h } from "./dom.js";
import { t } from "./i18n.js";

export const homeView = () => h("section", { class: "home" }, h("h1", {}, t("home title")));
