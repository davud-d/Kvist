// The inspector's panels. Each is a module of its own with the same shape:
//   id, title (an i18n key), icon, badge(agent) -> number|"", render(agent, el)
//   `account: true` marks a panel that is about the person, not a task: it also shows when no task is open.
// Add a file here and list it below; nothing else knows the panels.

import { context } from "./context.js";
import { helpers } from "./helpers.js";
import { hours } from "./hours.js";
import { jobs } from "./jobs.js";
import { screen } from "./screen.js";
import { server } from "./server.js";

export const panels = [hours, server, screen, context, helpers, jobs];

export const panelById = (id) => panels.find((panel) => panel.id === id) ?? panels[0];
