// Vendored from cloud/workspace/js/feed.js (the web workspace). Keep in step by hand.
// How a task's items are laid out: what the person and the agent said stand on their own, and so does what the
// agent thought (as in Kvist Glass: its thinking is part of the conversation, not of the work); everything it did
// between two of those (its calls, the muted notes) is one block of work that folds into a single line once it is
// over. A long history then reads as a conversation, not as a wall of calls. Pure data.

const WORK = new Set(["call", "note"]);

// A thought that ended with no words (the provider hides its thinking) has nothing to show and does not split
// the work around it.
const isSilent = (item) => item.kind === "thought" && !item.live && !String(item.text ?? "").trim();

const isWork = (item) => WORK.has(item.kind) && !(item.kind === "note" && item.tone === "error");

/** The items as blocks: an item, or {kind: "work", id, items, rev} for a run of work. */
export function feedBlocks(items) {
  const blocks = [];
  let run = null;
  for (const item of items) {
    if (isSilent(item)) continue;
    if (!isWork(item)) {
      run = null;
      blocks.push(item);
      continue;
    }
    if (!run) {
      run = { kind: "work", id: `w:${item.id}`, items: [], rev: "" };
      blocks.push(run);
    }
    run.items.push(item);
  }
  for (const block of blocks) if (block.kind === "work") block.rev = block.items.map((item) => `${item.id}.${item.rev}`).join(",");
  return blocks;
}

/** One line about a block of work: how many actions, how many failed, which files changed, whether it still runs. */
export function workSummary(items) {
  const calls = items.filter((item) => item.kind === "call");
  const files = new Set(calls.filter((call) => call.args?.path && (call.patch || call.stream || call.added)).map((call) => call.args.path));
  return {
    actions: calls.length,
    failed: calls.filter((call) => call.phase === "fail").length,
    files: [...files],
    running: items.some((item) => item.live === true || (item.kind === "call" && item.phase === "run")),
  };
}
