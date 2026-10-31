// The inspector's panels. Each is a module of its own with the same shape:
//   id, title (an i18n key), icon, badge(agent) -> number|"", render(agent, el)
// Add a file here and list it below; nothing else knows the panels.

import { context } from "./context.js";
import { helpers } from "./helpers.js";
import { jobs } from "./jobs.js";
import { screen } from "./screen.js";

export const panels = [helpers, screen, jobs, context];

export const panelById = (id) => panels.find((panel) => panel.id === id) ?? panels[0];
