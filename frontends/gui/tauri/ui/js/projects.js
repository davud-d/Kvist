// The rail's tree: each project folder opens into its sessions. Pure data in, rows out, so the
// shape (which sessions show, which one is lit) is testable without a page.

import { basename } from "./dom.js";
import { summary } from "./reducer.js";

const hasSession = (agent, name) => agent.session === name;

/**
 * `projects` are the folders the person added, `agents` every open agent (a core per session),
 * `listings` the sessions each folder holds as the core lists them, `expanded` the folders that are open.
 * A folder with an agent in it is always shown; a session that is open but not stored yet (a brand new
 * one is only stored with its first message) is listed first.
 */
export function buildTree({ projects, agents, listings, expanded, activeId }) {
  const workspaces = [...projects];
  for (const agent of agents) if (!workspaces.includes(agent.workspace)) workspaces.push(agent.workspace);

  return workspaces.map((workspace) => {
    const here = agents.filter((agent) => agent.workspace === workspace);
    const listed = listings.get(workspace) ?? [];
    const listedNames = new Set(listed.map((item) => item.name));
    const entry = (name, title, closed, agent) => ({
      session: name,
      title: agent?.title || title || "",
      closed: closed || (agent ? agent.closed !== null : false),
      agentId: agent?.id ?? null,
      state: agent ? summary(agent) : null,
      active: agent?.id === activeId,
      attention: agent?.attention === true,
    });
    const unsaved = here.filter((agent) => !listedNames.has(agent.session)).map((agent) => entry(agent.session, "", false, agent));
    const stored = listed.map((item) => entry(item.name, item.title || item.name, item.closed === true, here.find((agent) => hasSession(agent, item.name))));
    const sessions = [...unsaved, ...stored];
    return {
      workspace,
      name: basename(workspace),
      open: expanded.includes(workspace),
      sessions,
      running: sessions.filter((s) => s.state === "running" || s.state === "asks").length,
      active: sessions.some((s) => s.active),
    };
  });
}
