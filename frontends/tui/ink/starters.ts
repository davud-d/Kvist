// First-message suggestions: a person who has never used Kvist sees an empty prompt and no idea
// what to type. Three requests that fit what is in the folder are offered under the input until
// the first message is sent.
import { readdirSync } from "node:fs"

import { detectLocale, type Locale } from "./setup/strings.js"

export type WorkspaceKind = "empty" | "project" | "files"

export interface Starters {
  title: string
  hint: string
  prompts: string[]
}

// Kvist's own entries say nothing about what the person has put in the folder (the core's
// `_KVIST_OWN_ENTRIES`).
const OWN = new Set([".kvist", "KVIST.md", "KVIST-CHAT.md"])

const PROJECT_MARKERS = new Set([
  ".git",
  "package.json",
  "pyproject.toml",
  "requirements.txt",
  "setup.py",
  "Cargo.toml",
  "go.mod",
  "pom.xml",
  "build.gradle",
  "build.gradle.kts",
  "Gemfile",
  "composer.json",
  "CMakeLists.txt",
  "Makefile",
])

// `names` are the folder's top-level entries.
export function kindOf(names: readonly string[]): WorkspaceKind {
  const own = names.filter((name) => !OWN.has(name))
  if (own.length === 0) return "empty"
  return own.some((name) => PROJECT_MARKERS.has(name) || name.endsWith(".sln")) ? "project" : "files"
}

const COPY: Record<Locale, { title: string; hint: string; project: string[]; git: string; empty: string[]; files: string[] }> = {
  en: {
    title: "Not sure where to start? Try one:",
    hint: "↑↓ choose · Tab or Enter puts it in the box",
    project: ["Explain what this project does and how it is organized", "Look for bugs or risky places in the code", "Check how the tests are set up and tell me what is missing"],
    git: "Summarize what has changed in this project recently",
    empty: [
      "Create a simple to-do list app for the terminal in Python, with a README",
      "Set up a new project here: ask me what it should be",
      "Make a one-page personal website with HTML and CSS",
    ],
    files: [
      "Look through this folder and tell me what is here",
      "Suggest a tidier structure for these files, without moving anything yet",
      "Write a README that describes what is in this folder",
    ],
  },
  ru: {
    title: "Не знаете, с чего начать? Попробуйте:",
    hint: "↑↓ выбор · Tab или Enter вставляет в строку",
    project: ["Объясни, что делает этот проект и как он устроен", "Поищи ошибки и рискованные места в коде", "Посмотри, как устроены тесты, и скажи, чего не хватает"],
    git: "Расскажи, что менялось в этом проекте за последнее время",
    empty: [
      "Сделай простое приложение со списком дел для терминала на Python, с README",
      "Создай здесь новый проект: спроси меня, каким он должен быть",
      "Сделай личный сайт на одной странице на HTML и CSS",
    ],
    files: [
      "Просмотри эту папку и расскажи, что в ней лежит",
      "Предложи, как удобнее разложить эти файлы, пока ничего не перемещая",
      "Напиши README, который описывает содержимое этой папки",
    ],
  },
}

// Three requests for what the folder holds; a git project gets its history in place of the bug hunt.
export function startersFor(kind: WorkspaceKind, locale: Locale, git: boolean): Starters {
  const copy = COPY[locale]
  const prompts =
    kind === "empty" ? copy.empty : kind === "files" ? copy.files : git ? [copy.project[0]!, copy.git, copy.project[2]!] : copy.project
  return { title: copy.title, hint: copy.hint, prompts }
}

// Null when the folder cannot be read: there is nothing to base a suggestion on, and none is shown.
export function starterSet(workspace: string, env: NodeJS.ProcessEnv = process.env): Starters | null {
  let names: string[]
  try {
    names = readdirSync(workspace)
  } catch {
    // Missing or unreadable folder: the core reports it its own way.
    return null
  }
  return startersFor(kindOf(names), detectLocale(env), names.includes(".git"))
}
