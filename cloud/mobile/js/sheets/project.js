// A project is a folder on the agent's machine, optionally filled by cloning one of the person's GitHub repositories.

import { account, refresh } from "../account.js";
import { api, post } from "../api.js";
import { fill, h, icon } from "../dom.js";
import { repoLabel } from "../format.js";
import { t } from "../i18n.js";
import { openSheet, toast } from "../sheet.js";

/** The name a repository suggests: its last path part, without ".git". */
export const suggestedName = (repoName) => String(repoName).split("/").at(-1).replace(/\.git$/, "");

export function projectSheet(then = () => {}) {
  openSheet({
    title: t("new project"),
    variant: "tall",
    build: (close) => {
      const name = h("input", { class: "field", autocapitalize: "off", spellcheck: "false", placeholder: t("project name"), maxlength: "60" });
      const message = h("p", { class: "pay-status", role: "status" });
      const repos = h("div", { class: "choices" });
      let repo = "";
      const create = h("button", { class: "btn primary", type: "button" }, t("create"));

      const choose = (entry) => {
        repo = repo === entry.url ? "" : entry.url;
        if (repo && !name.value.trim()) name.value = suggestedName(entry.name);
        for (const row of repos.children) row.classList.toggle("on", row.dataset.url === repo);
      };
      api("/api/github/repos").then(({ repos: found }) => {
        if (!found.length) return;
        fill(repos, h("div", { class: "ways-label" }, t("from github")), found.map((entry) =>
          h("button", { class: "choice", type: "button", data: { url: entry.url }, onclick: () => choose(entry) },
            h("span", {}, repoLabel(entry.url) || entry.name),
            entry.private ? icon("lock", 14) : null)));
      }).catch(() => {});

      create.addEventListener("click", async () => {
        message.textContent = "";
        create.disabled = true;
        try {
          const project = await post("/api/projects", { name: name.value, repo_url: repo });
          await refresh("projects");
          toast(t("project created"));
          close();
          then(project);
        } catch (error) {
          message.textContent = error.code === "name_taken" ? t("name taken") : error.code === "bad_project_name" ? t("bad project name") : error.localized;
        } finally {
          create.disabled = false;
        }
      });

      return [h("p", { class: "dim" }, t("project intro")), h("div", { class: "field-group" }, h("label", { class: "label" }, t("name")), name), repos, message, create];
    },
  });
}

export const hasProjects = () => account.projects.length > 0;
