// The rail's tree: each project folder opens into its sessions. Pure data in, rows out, so the
// shape (which sessions show, which one is lit) is testable without a page.

import { summary } from "./reducer.js";

const hasSession = (agent, name) => agent.session === name;

/**
 * `projects` are the workspaces (project ids, and "" for the older tasks), `agents` every open agent (a core per session),
 * `listings` the sessions each folder holds as the core lists them, `expanded` the folders that are open.
 * A draft (an agent with no message sent) is not a session and is not listed: it is the "new task" row that
 * lights up, so opening the page does not leave a row behind for every visit.
 */
export function buildTree({ projects, agents, listings, expanded, activeId, nameOf }) {
  const workspaces = [...projects];
  for (const agent of agents) if (agent.session && !workspaces.includes(agent.workspace)) workspaces.push(agent.workspace);

  return workspaces.map((workspace) => {
    const here = agents.filter((agent) => agent.workspace === workspace);
    const listed = listings.get(workspace) ?? [];
    const listedNames = new Set(listed.map((item) => item.name));
    const publicIds = new Map(listed.map((item) => [item.name, item.public_id]));
    const entry = (name, title, closed, agent) => ({
      session: name,
      publicId: agent?.publicId || publicIds.get(name) || "",
      title: agent?.title || title || "",
      closed: closed || (agent ? agent.closed !== null : false),
      agentId: agent?.id ?? null,
      state: agent ? summary(agent) : null,
      active: agent?.id === activeId,
      attention: agent?.attention === true,
    });
    const unsaved = here.filter((agent) => agent.session && !listedNames.has(agent.session)).map((agent) => entry(agent.session, "", false, agent));
    const stored = listed.map((item) => entry(item.name, item.title || item.name, item.closed === true, here.find((agent) => hasSession(agent, item.name))));
    const sessions = [...unsaved, ...stored];
    return {
      workspace,
      name: nameOf(workspace),
      open: expanded.includes(workspace),
      sessions,
      running: sessions.filter((s) => s.state === "running" || s.state === "asks").length,
      active: sessions.some((s) => s.active),
      draftActive: here.some((agent) => !agent.session && agent.id === activeId),
    };
  });
}
