// The core asks, the person answers (docs/UI_API.md §7, `input_request`):
// approvals of commands and overwrites, menus, lines of text, secrets.

import { answer } from "../actions.js";
import { fill, h } from "../dom.js";
import { localized, t } from "../i18n.js";

function countdown(request) {
  if (!request.timeout_seconds) return null;
  const bar = h("div", { class: "countdown" }, h("i", { style: { animationDuration: `${request.timeout_seconds}s` } }));
  return bar;
}

function buttons(...list) {
  return h("div", { class: "dialog-buttons" }, list);
}

function button(label, onClick, variant = "") {
  return h("button", { class: `btn ${variant}`, type: "button", onclick: onClick }, label);
}

function diffRows(diff) {
  return h(
    "div",
    { class: "diff" },
    String(diff ?? "").split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++")).map((line) => h("div", { class: `diff-line ${line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "ctx"}` }, line)),
  );
}

function approval(agent, request) {
  const subject = request.subject;
  const done = (approved, remember = false) => answer(agent, request, { approved, remember });
  const body = subject.kind === "command"
    ? [h("pre", { class: "call-output" }, (subject.command ?? []).join(" ")), subject.reason ? h("p", { class: "dim" }, subject.reason) : null]
    : [h("div", { class: "dialog-path" }, subject.path), diffRows(subject.diff)];
  const always = request.remember_pattern ? button(`${t("allow always")}: ${request.remember_pattern}`, () => done(true, true)) : null;
  return {
    focus: ".btn.primary",
    title: localized(request.title) || (subject.kind === "command" ? "Run this command?" : "Overwrite this file?"),
    tone: "warn",
    content: [...body, countdown(request), buttons(button(t("allow once"), () => done(true), "primary"), always, button(t("deny"), () => done(false)))],
    cancel: () => done(false),
  };
}

function confirmDialog(agent, request) {
  const done = (approved) => answer(agent, request, { approved });
  return {
    focus: ".btn.primary",
    title: localized(request.title),
    content: [request.body ? h("pre", { class: "call-output" }, localized(request.body)) : null, countdown(request), buttons(button(t("yes"), () => done(true), "primary"), button(t("no"), () => done(false)))],
    cancel: () => done(false),
  };
}

function choiceDialog(agent, request) {
  const options = request.options ?? [];
  let shown = options;
  let index = Math.max(0, options.findIndex((o) => o.value === request.current_value));
  const list = h("div", { class: "choice-list", role: "listbox" });
  const filter = h("input", { class: "field", placeholder: "…", spellcheck: "false", autocomplete: "off" });
  const choose = (option) => answer(agent, request, { value: option.value });
  const paint = () => {
    fill(list, 
      ...shown.map((option, i) =>
        h("div", { class: `choice${i === index ? " on" : ""}`, role: "option", onclick: () => choose(option), onmousemove: () => { index = i; paint(); } }, h("span", { class: "choice-label" }, localized(option.label ?? option.value)), option.description ? h("span", { class: "choice-note" }, localized(option.description)) : null),
      ),
    );
    list.children[index]?.scrollIntoView({ block: "nearest" });
  };
  filter.addEventListener("input", () => {
    const q = filter.value.toLowerCase();
    shown = options.filter((o) => `${o.label ?? ""} ${o.value} ${o.description ?? ""}`.toLowerCase().includes(q));
    index = 0;
    paint();
  });
  filter.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") index = Math.min(shown.length - 1, index + 1);
    else if (event.key === "ArrowUp") index = Math.max(0, index - 1);
    else if (event.key === "Enter" && shown[index]) return choose(shown[index]);
    else return undefined;
    event.preventDefault();
    paint();
    return undefined;
  });
  paint();
  return {
    focus: "input",
    title: localized(request.title),
    content: [request.help_text ? h("p", { class: "dim" }, localized(request.help_text)) : null, options.length > 7 ? filter : null, list, countdown(request)],
    cancel: () => answer(agent, request, { value: null }),
  };
}

function textDialog(agent, request) {
  const secret = request.kind === "secret";
  const input = h("input", { class: "field", type: secret ? "password" : "text", value: secret ? "" : (request.current_value ?? ""), spellcheck: "false", autocomplete: "off" });
  const submit = () => answer(agent, request, { value: input.value });
  input.addEventListener("keydown", (event) => event.key === "Enter" && submit());
  return {
    focus: "input",
    title: localized(request.title),
    content: [request.body ? h("p", { class: "dim" }, localized(request.body)) : null, request.help_text ? h("p", { class: "dim" }, localized(request.help_text)) : null, input, buttons(button(t("ok"), submit, "primary"), button(t("cancel"), () => answer(agent, request, { value: null })))],
    cancel: () => answer(agent, request, { value: null }),
  };
}

function build(agent, request) {
  if (request.subject) return approval(agent, request);
  if (request.kind === "confirm") return confirmDialog(agent, request);
  if (request.kind === "choice") return choiceDialog(agent, request);
  return textDialog(agent, request);
}

/** The modal for the agent's oldest open request, or null. */
export function makeDialog(agent) {
  const request = agent.dialogs[0];
  if (!request) return null;
  const spec = build(agent, request);
  const card = h("div", { class: `dialog px ${spec.tone ?? ""}`, role: "dialog", "aria-modal": "true", tabindex: "-1" }, h("div", { class: "dialog-title" }, spec.title), ...spec.content);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      spec.cancel();
    }
  });
  const scrim = h("div", { class: "scrim", data: { request: request.request_id } }, card);
  queueMicrotask(() => (card.querySelector(spec.focus) ?? card).focus());
  return scrim;
}
