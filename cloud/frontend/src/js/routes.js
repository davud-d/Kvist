// Path → screen. The pricing page is not here: it is prerendered HTML served by the server.
import { adminScreen } from "./screens/admin.js";
import { dashboardScreen } from "./screens/dashboard.js";
import { onboardingScreen } from "./screens/onboarding.js";
import { settingsScreen } from "./screens/settings.js";
import { startScreen } from "./screens/start.js";
import { taskScreen } from "./screens/task.js";

export const routes = [
  { pattern: /^\/start\/?$/, screen: startScreen, title: "start.title", nav: null, funnel: true },
  { pattern: /^\/onboarding\/?$/, screen: onboardingScreen, title: "onboarding.title", nav: "/new", funnel: true },
  { pattern: /^\/new\/?$/, screen: onboardingScreen, title: "onboarding.newTitle", nav: "/new" },
  { pattern: /^\/dashboard\/?$/, screen: dashboardScreen, title: "nav.dashboard", nav: "/dashboard" },
  { pattern: /^\/tasks\/(\d+)\/?$/, screen: taskScreen, title: "task.goal", nav: null },
  { pattern: /^\/settings\/?$/, screen: settingsScreen, title: "nav.settings", nav: "/settings" },
  { pattern: /^\/admin\/?$/, screen: adminScreen, title: "admin.title", nav: "/admin" },
];
