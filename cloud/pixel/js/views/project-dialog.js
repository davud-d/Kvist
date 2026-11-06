// "Add project": a project is a named folder on the person's server. It is either a clone of one of their
// GitHub repositories, or just a name, in which case an empty folder of that name is made. Names never
// repeat (compared without regard to case, spaces and hyphens alike), so the dialog says so as you type;
// the server makes the final decision.

import { addProject } from "../actions.js";
import { account, api, createProject, loginUrl, nameProblem, NAME_LIMIT, suggestedName } from "../cloud.js";
import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { openModal } from "./modal.js";

let repos = null;

/** The person's public repositories, asked for once per page load. */
async function loadRepos() {
  if (repos) return repos;
  try {
    repos = await api("/api/github/repos");
  } catch (error) {
    repos = { repos: [], reason: "unreachable", message: error.localized ?? String(error) };
  }
  return repos;
}

export function openProjectDialog() {
  const choice = { source: "name", repo: null };
  const mode = h("div", { class: "segment" });
  const list = h("div", { class: "choice-list repo-list" });
  const name = h("input", { class: "field", type: "text", maxlength: String(NAME_LIMIT), spellcheck: "false", autocomplete: "off", placeholder: t("project name") });
  const hint = h("p", { class: "dim" });
  const create = h("button", { class: "btn primary", type: "button" }, t("create"));
  const modal = openModal({
    title: t("add project"),
    content: [mode, list, h("label", { class: "field-stack" }, h("span", { class: "dim" }, t("project name")), name), hint, h("div", { class: "dialog-buttons" }, create, h("button", { class: "btn", type: "button", onclick: () => modal.close() }, t("cancel")))],
  });

  // What the name field says about itself, and whether Create can go.
  const check = () => {
    const problem = name.value.trim() ? nameProblem(name.value, account.projects) : "";
    hint.classList.toggle("bad", Boolean(problem));
    hint.textContent = problem ? t(problem) : t("project folder note");
    create.disabled = !name.value.trim() || Boolean(problem) || (choice.source === "github" && !choice.repo);
  };

  async function paintRepos() {
    fill(list, h("p", { class: "dim" }, t("loading")));
    const found = await loadRepos();
    if (choice.source !== "github") return;
    if (found.reason === "no_token" || found.reason === "token_expired") {
      fill(list, h("p", { class: "dim" }, t("github sign in again")), h("a", { class: "btn", href: loginUrl() }, "GitHub"));
    } else if (found.reason) {
      fill(list, h("p", { class: "dim" }, found.message ?? t("github unreachable")));
    } else if (!found.repos.length) {
      fill(list, h("p", { class: "dim" }, t("no github repos")));
    } else {
      fill(list, found.repos.map((repo) => h("div", { class: `choice${choice.repo?.url === repo.url ? " on" : ""}`, onclick: () => pick(repo) }, h("span", { class: "choice-label" }, repo.name), repo.description ? h("span", { class: "choice-note" }, repo.description) : null)));
    }
    check();
  }

  function pick(repo) {
    choice.repo = repo;
    name.value = suggestedName(repo.name);
    paintRepos();
    name.focus();
  }

  function paintMode() {
    fill(mode, [["github", "from github"], ["name", "new project"]].map(([value, label]) => h("button", { class: `seg${choice.source === value ? " on" : ""}`, type: "button", onclick: () => { choice.source = value; if (value === "name") choice.repo = null; paintMode(); } }, t(label))));
    list.hidden = choice.source !== "github";
    if (choice.source === "github") paintRepos();
    check();
  }

  const submit = async () => {
    if (create.disabled) return;
    create.disabled = true;
    try {
      const project = await createProject(name.value.trim(), choice.repo?.url ?? "");
      modal.close();
      addProject(String(project.id));
    } catch (error) {
      hint.classList.add("bad");
      hint.textContent = ["name_taken", "bad_project_name", "bad_repo"].includes(error.code) ? t(error.code) : error.localized ?? String(error);
      create.disabled = false;
    }
  };
  create.addEventListener("click", submit);
  name.addEventListener("input", check);
  name.addEventListener("keydown", (event) => event.key === "Enter" && submit());
  paintMode();
  return modal;
}
