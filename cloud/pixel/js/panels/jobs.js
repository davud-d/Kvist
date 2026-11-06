// Background commands the agent started (`job_*` events).

import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";

export const jobs = {
  id: "jobs",
  title: "jobs",
  icon: "term",
  badge: (agent) => [...agent.jobs.values()].filter((job) => job.state === "running").length || "",
  render(agent, el) {
    const all = [...agent.jobs.values()].reverse();
    fill(el, 
      ...(all.length
        ? all.map((job) =>
            h("div", { class: `job px ${job.state}` }, h("div", { class: "helper-head" }, icon("term", 14), h("span", { class: "helper-task" }, job.purpose || job.command.join(" ")), h("span", { class: "chip" }, job.exitCode === null ? job.state : `${job.state} · ${job.exitCode}`)), job.output ? h("pre", { class: "call-output" }, job.output.split("\n").slice(-8).join("\n")) : null),
          )
        : [h("p", { class: "dim pad" }, t("no jobs"))]),
    );
  },
};
