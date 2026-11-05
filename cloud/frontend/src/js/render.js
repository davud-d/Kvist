// DOM for the pieces of the task feed. Everything is built as text nodes; nothing Kvist sends is
// ever parsed as HTML.
import { h } from "./dom.js";
import { blocks, inline } from "./events.js";
import { commandLine, diffLines } from "./format.js";
import { t } from "./i18n.js";

export function markdown(text) {
  return h("div.say", {}, blocks(text).map((block) => (block.kind === "code"
    ? h("pre.out", {}, block.text)
    : h("p", {}, inline(block.text).map(([kind, part]) => (kind === "code" ? h("code", {}, part) : part))))));
}

export function diff(patch) {
  return h("pre.diff", {}, diffLines(patch).map((line) =>
    h(`span${line.kind === "add" ? ".add" : line.kind === "del" ? ".del" : line.kind === "hunk" ? ".hunk" : ""}`,
      {}, `${line.text}\n`)));
}

function actionNode(item) {
  const result = item.result;
  const state = item.running ? ["mark-run", "●"] : result && result.ok === false ? ["mark-err", "✗"] : ["mark-ok", "✓"];
  const shell = item.type === "run_shell";
  const command = shell ? commandLine(item.args) : "";
  const output = result ? result.output : item.live;
  return h("li.action", {},
    h(`span.${state[0]}`, { "aria-hidden": "true" }, state[1]),
    h("div", {},
      h("p.what", {}, item.description || item.type, command ? [" ", h("code", {}, command)] : null),
      result && result.summary ? h("p.sum", {}, result.summary) : null,
      shell && output ? h("details.fold", {},
        h("summary", {}, item.running ? t("task.liveOutput") : t("task.output")), h("pre.out", {}, output)) : null,
      result && result.patch ? h("details.fold", {},
        h("summary", {}, `${t("task.diff")} · ${result.path || ""} +${result.lines_added || 0} −${result.lines_removed || 0}`),
        diff(result.patch)) : null));
}

function noteNode(event) {
  if (event.type === "message") {
    return event.source === "notice" ? h("p.notice", {}, event.markdown || "") : markdown(event.markdown || "");
  }
  if (event.type === "error") return h("p.hint.err", {}, event.message || "");
  if (event.type === "job_started") {
    return h("p.notice", {}, t("task.jobStarted", { cmd: commandLine({ command: event.command }) || event.purpose }));
  }
  if (event.type === "job_finished") return h("p.notice", {}, t("task.jobFinished", { status: event.status }));
  return h("p.notice", {}, event.text || "");
}

export function groupNode(group) {
  const reasoning = group.reasoning;
  return h("li", {},
    h("article.group", { "aria-label": t("task.step", { n: group.n }) },
      h("header.group-head", {}, t("task.step", { n: group.n })),
      h("div.group-body", {},
        reasoning && reasoning.live
          ? h("div", {}, h("p.notice", {}, t("task.thinking")), reasoning.text ? h("p.thought", {}, reasoning.text.slice(-600)) : null)
          : reasoning && reasoning.text
            ? h("details.fold", {}, h("summary", {}, t("task.thoughtFor", { s: Math.round(reasoning.seconds || 0) })),
                h("p.thought", {}, reasoning.text))
            : reasoning && reasoning.seconds ? h("p.notice", {}, t("task.thoughtFor", { s: Math.round(reasoning.seconds) })) : null,
        group.plan ? h("details.fold", { open: true }, h("summary", {}, t("task.plan")), markdown(group.plan)) : null,
        group.answer && group.answer.text ? markdown(group.answer.text) : group.say ? markdown(group.say) : null,
        group.actions.length ? h("ul.actions", {}, group.actions.map(actionNode)) : null,
        group.notes.map(noteNode))));
}
