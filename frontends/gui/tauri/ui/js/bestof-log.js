// The read-only journal of one best-of executor. Its child session is read the way a switched-to session's history is
// (`--session-op history`) and its events are replayed through the same `reduce` into an agent of their own, so the
// conversation in front is never touched. Nothing here talks back to the core.

import { createAgent, replayHistory } from "./reducer.js";

// The newest events, as many as the Classic terminal interface reads.
export const LOG_EVENTS = 500;

/** A call the journal never finished was stopped, not still running. */
function settle(view) {
  for (const item of view.items) {
    if (item.kind !== "call" || item.phase !== "run") continue;
    item.phase = "fail";
    item.summary ||= "stopped";
    item.rev++;
  }
}

/**
 * Opens the log of `executor` over the arena and reads its journal with `fetchHistory(session)`. `changed()` is called
 * when the log appears and when it is read. A log closed, or replaced by another, while it was being read is dropped.
 */
export async function openExecutorLog(agent, executor, fetchHistory, changed) {
  const state = agent.bestof;
  const log = { id: executor.id, style: executor.style, session: executor.session, status: "loading", error: "", older: false, view: createAgent(`log:${executor.id}`, agent.workspace, executor.session) };
  state.log = log;
  changed();
  try {
    const reply = await fetchHistory(executor.session);
    if (state.log !== log) return;
    if (reply?.ok === false) throw new Error(String(reply.error ?? ""));
    replayHistory(log.view, reply?.events ?? []);
    settle(log.view);
    log.older = reply?.has_more === true;
    log.status = "ready";
  } catch (error) {
    if (state.log !== log) return;
    log.status = "failed";
    log.error = error instanceof Error ? error.message : String(error);
  }
  changed();
}
