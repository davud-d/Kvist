// The orb: where the agent is right now, at the end of the conversation. It replaces the text caret. While the
// model speaks it is a blue dot that breathes at the end of its words; otherwise it stands on its own line and
// shows what the model is about, from the moment the call is planned (a call_begin names the tool before its
// arguments arrive):
//
//   thinking (and the first moments of a turn)  a faint brain, breathing
//   a terminal command                          a terminal whose prompt waits, the cursor blinking
//   reading                                     a book whose page turns
//   adding to a file (write, append)            an open book, a line being written at the end
//   changing a file (replace)                   a line rubbed out with an eraser, then written again
//   looking through folders                     a folder opening
//   searching                                   a magnifier looking around
//   the web                                     a turning globe
//   a skill                                     a library: one book drawn out of a shelf of many
//   helpers                                     two dots circling a third
//
// Every drawing loops on one period; the orb is moved and redrawn often (each chunk of a reply repaints the text),
// so its loop is set on the page's clock and a redraw picks up where the last one stood instead of starting over.

import { animate } from "../motion.js";

const BOOK = '<path class="pg" d="M12 6.5C10 5.3 7.2 5 4.5 5.6v12c2.7-.6 5.5-.3 7.5.9"/><path class="pg" d="M12 6.5c2-1.2 4.8-1.5 7.5-.9v12c-2.7-.6-5.5-.3-7.5.9z"/>';

const DRAWINGS = {
  speak: { period: 1100, svg: "" },
  think: {
    period: 2400,
    svg: '<path d="M9.5 4.5A2.6 2.6 0 0 0 7 7a2.8 2.8 0 0 0-2.2 4.4A2.9 2.9 0 0 0 6.6 16a2.7 2.7 0 0 0 5.4.8V6.6a2.2 2.2 0 0 0-2.5-2.1z"/><path d="M14.5 4.5A2.6 2.6 0 0 1 17 7a2.8 2.8 0 0 1 2.2 4.4 2.9 2.9 0 0 1-1.8 4.6 2.7 2.7 0 0 1-5.4.8V6.6a2.2 2.2 0 0 1 2.5-2.1z"/><path class="spark" d="M8.5 10.5c1 .3 1.6 1 1.8 2M15.5 10.5c-1 .3-1.6 1-1.8 2"/>',
  },
  shell: {
    period: 1600,
    svg: '<rect x="3" y="4.5" width="18" height="15" rx="3"/><path class="prompt" d="M7 10l2.5 2.2L7 14.4"/><path class="cursor" d="M11.5 15h4"/>',
  },
  read: {
    period: 2000,
    svg: `${BOOK}<path class="leaf" d="M12 6.5c2-1.2 4.8-1.5 7.5-.9v12c-2.7-.6-5.5-.3-7.5.9z"/>`,
  },
  write: {
    period: 2000,
    svg: `${BOOK}<path class="ln" d="M14 9h3.5"/><path class="ln" d="M14 11.5h3.5"/><path class="ink" pathLength="1" d="M14 14h3.5"/><path class="pen" d="M17.5 12.2l1.6-1.6 1 1-1.6 1.6z"/>`,
  },
  edit: {
    period: 2800,
    svg: `${BOOK}<path class="ln" d="M14 9h3.5"/><path class="ink redo" pathLength="1" d="M14 12.5h3.5"/><rect class="eraser" x="12.6" y="11" width="2.6" height="3" rx=".6"/><path class="pen" d="M17.5 10.7l1.6-1.6 1 1-1.6 1.6z"/>`,
  },
  folder: {
    period: 2000,
    svg: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.2l2 2.2h7.8a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path class="lid" d="M3.5 10.5h17"/>',
  },
  search: {
    period: 1800,
    svg: '<g class="lens"><circle cx="10.5" cy="10.5" r="5"/><path d="M14.3 14.3l4 4"/></g>',
  },
  web: {
    period: 2400,
    svg: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><ellipse class="meridian" cx="12" cy="12" rx="3.6" ry="8.5"/>',
  },
  skill: {
    period: 2800,
    svg: '<path d="M2.5 19.5h19"/><path class="row" d="M4 19.5V9.5M6.4 19.5V8M8.8 19.5V10M15.2 19.5V8.5M17.6 19.5V10.5M20 19.5V8"/><rect class="drawn" x="11" y="7.5" width="2" height="12" rx=".6"/>',
  },
  helper: {
    period: 2000,
    svg: '<circle class="hub" cx="12" cy="12" r="2"/><g class="ring"><circle cx="12" cy="5.5" r="1.6"/><circle cx="12" cy="18.5" r="1.6"/></g>',
  },
};

const BY_ACTION = {
  read_file: "read", read_multiple_files: "read", memory: "read",
  write_file: "write", append_file: "write",
  replace_in_file: "edit",
  run_shell: "shell", job_output: "shell", job_stop: "shell", git_status: "shell", git_diff: "shell",
  list_dir: "folder", list_tree: "folder",
  find_files: "search", search_code: "search",
  web_search: "web", fetch_url: "web",
  skills: "skill", skill_call: "skill",
  delegate_task: "helper", helper: "helper",
};

/** What the orb shows for this task now; null when the agent is not at work. */
export function orbState(agent) {
  if (!agent || agent.state !== "running") return null;
  const last = agent.items.at(-1);
  if (last?.kind === "call" && last.phase === "run") return BY_ACTION[last.actionType] ?? "think";
  if (last?.kind === "reply" && last.live) return "speak";
  return "think";
}

export function makeOrb() {
  const el = document.createElement("span");
  el.className = "orb";
  el.setAttribute("aria-hidden", "true");
  return el;
}

/**
 * Puts the orb's loop on the page's clock. Call it whenever the orb was put into the page again or redrawn: its
 * CSS animations restart then, and this makes them start where the clock stands.
 */
export function syncOrb(el) {
  const period = DRAWINGS[el.dataset.state]?.period ?? 2000;
  el.style.setProperty("--orb-period", `${period}ms`);
  el.style.setProperty("--orb-phase", `-${Math.round(performance.now() % period)}ms`);
}

/** Shows `state` on the orb; a new drawing fades in. */
export function setOrb(el, state) {
  if (el.dataset.state === state) return;
  const drawing = DRAWINGS[state] ?? DRAWINGS.think;
  el.dataset.state = state;
  el.innerHTML = state === "speak" ? '<span class="orb-dot"></span>' : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${drawing.svg}</svg>`;
  syncOrb(el);
  animate(el.firstChild, [{ opacity: 0, transform: "scale(0.8)" }, { opacity: 1, transform: "none" }], { ms: 180 });
}
