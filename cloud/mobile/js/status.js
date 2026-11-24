// Where the agent is right now, at the end of the conversation, as the web workspace shows it: the orb (a blue dot that
// breathes while the model speaks, a small drawing of what it is doing otherwise) and the line with the rippling word, how
// long it has been and what it is at. The word and its colour come from js/web/spinner.js: the colour warms from blue to
// amber to red the longer the model keeps the person waiting.

import { feedActivity } from "./feed.js";
import { BY_ACTION, makeOrb, setOrb } from "./web/orb.js";
import { coolDown, formatSeconds, spinnerWord } from "./web/spinner.js";
import { h } from "./dom.js";
import { inPhase } from "./web/phase.js";

const TICK_MS = 100;

/** What the orb shows for this feed now; null when the agent is not at work. */
export function orbStateFor(feed) {
  const activity = feedActivity(feed);
  if (!activity) return null;
  if (activity.running) return BY_ACTION[activity.running.actionType] ?? "think";
  if (activity.speaking) return "speak";
  return "think";
}

/** The element at the end of the feed; `update(feed)` redraws it, and it keeps its own clock between events. */
export function createStatus() {
  const node = h("div", { class: "tail orb-scope" });
  const orb = makeOrb();
  const row = h("div", { class: "tail-status" });
  let feedRef = null;
  let shownWait = 0;
  let shownAt = Date.now();
  let label = "";

  function paint() {
    const state = feedRef ? orbStateFor(feedRef) : null;
    if (!state) {
      shownWait = 0;
      label = "";
      node.replaceChildren();
      orb.dataset.state = "";
      return;
    }
    if (!node.firstChild) node.append(h("div", { class: "orb-row" }, orb), row);
    setOrb(orb, state);
    const now = Date.now();
    const activity = feedActivity(feedRef);
    // The wait that warms the colour counts only while the model is silent: a running command is not the model's delay.
    const target = activity.silentSince && !activity.running ? (now - activity.silentSince) / 1000 : 0;
    shownWait = coolDown(shownWait, target, (now - shownAt) / 1000);
    shownAt = now;
    const elapsed = (now - activity.startedAt) / 1000;
    const word = `${spinnerWord("coder", activity.startedAt, elapsed, activity.request)}…`;
    const tone = shownWait > 45 ? " stuck" : shownWait > 5 ? " slow" : "";
    if (label !== word + tone) {
      label = word + tone;
      row.replaceChildren(inPhase(h("span", { class: `shimmer${tone}` }, word), 2400), h("span", { class: "status-detail" }));
    }
    const doing = activity.running?.label ?? feedRef.status.text ?? "";
    row.lastChild.textContent = [formatSeconds(elapsed), doing.slice(0, 60)].filter(Boolean).join(" · ");
  }
  setInterval(paint, TICK_MS);

  return {
    node,
    update(feed) {
      feedRef = feed;
      paint();
    },
  };
}
