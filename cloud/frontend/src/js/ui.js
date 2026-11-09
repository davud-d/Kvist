// Pieces every screen shares: the error block, the empty block, status pills.
import { h } from "./dom.js";
import { t } from "./i18n.js";

/** An error with what to do about it, and the support code when the server failed. */
export function errorBox(error, retry) {
  const code = (error && error.code) || "fallback";
  const text = t(`errors.${code}`);
  return h("div.banner.err", { role: "alert" },
    h("p.title", { text: typeof text === "string" && !text.startsWith("errors.") ? text : t("errors.fallback") }),
    error && error.supportCode ? h("p.code", { text: t("common.supportCode", { code: error.supportCode }) }) : null,
    code === "not_signed_in"
      ? h("p", {}, h("a.btn.ghost", { href: "/auth/github/login?next=" + encodeURIComponent(location.pathname), text: "GitHub" }))
      : retry ? h("p", {}, h("button.btn.ghost", { type: "button", onclick: retry, text: t("common.retry") })) : null);
}

export function emptyBox(text, action) {
  return h("div.empty", {}, h("p", { text }), action ? h("p", { style: "margin-top:12px" }, action) : null);
}

const TASK_PILL = {
  queued: "", starting: "working", running: "working", waiting_for_user: "waiting",
  paused: "", done: "done", cancelled: "", failed: "failed",
};

export function taskPill(status) {
  return h(`span.pill.${TASK_PILL[status] || "idle"}`, {}, h("span.led", { "aria-hidden": "true" }),
    t(`taskStatus.${status}`));
}

const MACHINE_PILL = {
  creating: "waking", waking: "waking", working: "working", waiting_for_you: "waiting",
  ready: "ready", awake: "ready", error: "failed", suspended: "failed",
};

export function machinePill(status) {
  return h(`span.pill.${MACHINE_PILL[status] || "idle"}`, {}, h("span.led", { "aria-hidden": "true" }),
    t(`machineStatus.${status}`));
}

/** Announced to screen readers when it changes. */
export function liveRegion() {
  return h("p.sr", { "aria-live": "polite", role: "status" });
}
