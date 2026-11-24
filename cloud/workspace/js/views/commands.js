// A small hint over the message field once it starts with "/". The commands are Kvist's own: the list its core
// announced (its manifest: live from the task, or the last one the cloud kept), less what the cloud takes away
// (`account.commands.hidden`). A few of them the page runs itself, because the cloud owns what they change
// (actions.js, PAGE_COMMANDS); the rest go to Kvist through the task. Plugin commands are words Kvist expands.
// After "/effort " the hint offers its levels. Arrows choose, Tab or Enter takes the choice, Esc closes it.

import { EFFORT_LEVELS, PAGE_COMMANDS } from "../actions.js";
import { fill, h } from "../dom.js";
import { effortWord } from "./effort.js";

/** What Kvist's manifest offers here: {name, about}, its own commands first, then the plugins. */
export function offeredCommands(manifest, hidden = []) {
  const own = (manifest?.slash_commands ?? []).filter((entry) => PAGE_COMMANDS.has(entry.name) || (entry.via === "core" && !hidden.includes(entry.name)));
  const plugins = manifest?.plugin_commands ?? [];
  return [...own, ...plugins].map((entry) => ({ name: String(entry.name).replace(/^\//, ""), about: String(entry.description ?? "") }));
}

/** What the hint offers for what is typed: [{insert, label, about, send}], empty when it has nothing to say. */
export function commandChoices(text, offered) {
  if (!text.startsWith("/") || text.includes("\n")) return [];
  const level = text.match(/^\/effort\s+(\S*)$/i);
  if (level && offered.some((command) => command.name === "effort")) {
    const typed = level[1].toLowerCase();
    return EFFORT_LEVELS.filter((value) => value.startsWith(typed) || effortWord(value).toLowerCase().startsWith(typed))
      .map((value) => ({ insert: `/effort ${value}`, label: value, about: effortWord(value), send: true }));
  }
  if (/\s/.test(text)) return [];
  const typed = text.slice(1).toLowerCase();
  return offered.filter((command) => command.name.startsWith(typed))
    .map((command) => ({ insert: `/${command.name} `, label: `/${command.name}`, about: command.about, send: false }));
}

/**
 * Hangs the hint on `input` (inside `host`, which is positioned). `submit` sends the field; a level of /effort is
 * sent as soon as it is taken, a command is completed and waits for its argument. `offered` says, each time the
 * hint opens, which commands this field offers (offeredCommands).
 */
export function attachCommandHint(input, host, submit, offered) {
  const list = h("div", { class: "menu command-hint", role: "listbox", hidden: true });
  host.append(list);
  let choices = [];
  let at = 0;

  const paint = () => {
    list.hidden = choices.length === 0;
    fill(list, choices.map((choice, i) => h("button", {
      class: `command-row${i === at ? " on" : ""}`, type: "button", role: "option", "aria-selected": String(i === at),
      onpointerdown: (event) => { event.preventDefault(); take(i); },
    }, h("span", { class: "command-name" }, choice.label), h("span", { class: "command-about" }, choice.about))));
  };
  const refresh = () => {
    choices = commandChoices(input.value, offered());
    at = Math.min(at, Math.max(0, choices.length - 1));
    paint();
  };
  const take = (i) => {
    const choice = choices[i];
    if (!choice) return;
    input.value = choice.insert;
    input.dispatchEvent(new Event("input"));
    // Sending empties the field without an input event; the hint closes once it is sent.
    if (choice.send) Promise.resolve(submit()).then(refresh);
    else refresh();
    input.focus();
  };

  input.addEventListener("input", refresh);
  input.addEventListener("blur", () => { choices = []; paint(); });
  // Before the field's own keys (history, send): the hint answers them while it is open.
  input.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (step) {
      at = (at + step + choices.length) % choices.length;
      paint();
    } else if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) take(at);
    else if (event.key === "Escape") {
      choices = [];
      paint();
    } else return;
    event.preventDefault();
    event.stopImmediatePropagation();
  });
}
