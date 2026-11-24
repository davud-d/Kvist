// The agent asks, the person answers (input_request): approve a command or an overwrite, confirm, pick from a
// list, type a line or a secret. The card stands where the composer was, so the conversation above stays readable.

import { h, icon } from "./dom.js";
import { localized } from "./format.js";
import { t } from "./i18n.js";

const text = (value) => (value && typeof value === "object" ? localized(value[document.documentElement.lang] ?? value.en ?? "") : localized(value));

const buttons = (...list) => h("div", { class: "dialog-buttons" }, list.filter(Boolean));
const button = (label, onclick, variant = "") => h("button", { class: `btn ${variant}`, type: "button", onclick }, label);

function diff(raw) {
  const lines = String(raw ?? "").split("\n").filter((line) => !line.startsWith("---") && !line.startsWith("+++")).slice(0, 80);
  return h("pre", { class: "diff" }, lines.map((line) =>
    h("span", { class: line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "" }, `${line}\n`)));
}

function approval(request, answer) {
  const subject = request.subject;
  const done = (approved, remember = false) => answer({ approved, remember });
  const body = subject.kind === "command"
    ? [h("pre", { class: "call-out" }, (subject.command ?? []).join(" ")), subject.reason ? h("p", { class: "dim" }, subject.reason) : null]
    : [h("div", { class: "dialog-path" }, subject.path), diff(subject.diff)];
  return h("div", { class: "dialog warn", role: "dialog" },
    h("div", { class: "dialog-title" }, icon("alert", 18), text(request.title) || (subject.kind === "command" ? t("run command") : t("overwrite file"))),
    ...body,
    buttons(
      button(t("allow once"), () => done(true), "primary"),
      request.remember_pattern ? button(`${t("allow always")} · ${request.remember_pattern}`, () => done(true, true)) : null,
      button(t("deny"), () => done(false))));
}

function confirm(request, answer) {
  return h("div", { class: "dialog", role: "dialog" },
    h("div", { class: "dialog-title" }, text(request.title)),
    request.body ? h("p", { class: "dim" }, text(request.body)) : null,
    buttons(button(t("yes"), () => answer({ approved: true }), "primary"), button(t("no"), () => answer({ approved: false }))));
}

function choice(request, answer) {
  const options = request.options ?? [];
  return h("div", { class: "dialog", role: "dialog" },
    h("div", { class: "dialog-title" }, text(request.title)),
    request.help_text ? h("p", { class: "dim" }, text(request.help_text)) : null,
    h("div", { class: "choices" }, options.map((option) =>
      h("button", { class: `choice${option.value === request.current_value ? " on" : ""}`, type: "button", onclick: () => answer({ value: option.value }) },
        h("span", {}, text(option.label ?? option.value)),
        option.description ? h("small", {}, text(option.description)) : null))),
    buttons(button(t("cancel"), () => answer({ value: null }))));
}

function line(request, answer) {
  const secret = request.kind === "secret";
  const input = h("input", { class: "field", type: secret ? "password" : "text", value: secret ? "" : (request.current_value ?? ""),
    autocomplete: "off", autocapitalize: "off", spellcheck: "false", enterkeyhint: "done" });
  const submit = () => answer({ value: input.value });
  input.addEventListener("keydown", (event) => event.key === "Enter" && submit());
  return h("div", { class: "dialog", role: "dialog" },
    h("div", { class: "dialog-title" }, text(request.title)),
    request.body ? h("p", { class: "dim" }, text(request.body)) : null,
    request.help_text ? h("p", { class: "dim" }, text(request.help_text)) : null,
    input,
    buttons(button(t("ok"), submit, "primary"), button(t("cancel"), () => answer({ value: null }))));
}

/** The card for the oldest open request; `answer(reply)` sends {approved} or {value} back. */
export function dialogFor(request, answer) {
  if (request.subject) return approval(request, answer);
  if (request.kind === "confirm") return confirm(request, answer);
  if (request.kind === "choice") return choice(request, answer);
  return line(request, answer);
}
