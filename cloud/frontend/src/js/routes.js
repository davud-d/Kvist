// Path → screen. The pricing page is not here: it is prerendered HTML served by the server.
import { h } from "./dom.js";
import { t } from "./i18n.js";
import { onboardingScreen } from "./screens/onboarding.js";
import { startScreen } from "./screens/start.js";
import { emptyBox } from "./ui.js";

function placeholder(root) {
  root.append(h("h1.h2", { text: t("nav.dashboard") }), emptyBox(t("dashboard.noTasks")));
}

export const routes = [
  { pattern: /^\/start\/?$/, screen: startScreen, title: "start.title", nav: null, funnel: true },
  { pattern: /^\/onboarding\/?$/, screen: onboardingScreen, title: "onboarding.title", nav: "/new", funnel: true },
  { pattern: /^\/new\/?$/, screen: onboardingScreen, title: "onboarding.newTitle", nav: "/new" },
  { pattern: /^\/dashboard\/?$/, screen: placeholder, title: "nav.dashboard", nav: "/dashboard" },
];
