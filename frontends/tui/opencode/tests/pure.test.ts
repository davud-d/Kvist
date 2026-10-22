import { describe, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

import { changedFiles } from "../src/screens/sidebar"
import { visibleItems } from "../src/screens/session"
import { matchSlash } from "../src/screens/prompt"
import { buttonLabel, noticeWords } from "../src/screens/closed"
import { ago, sessionOptions } from "../src/screens/dialogs"
import { isOffered, mentionAt } from "../src/files"
import { DEFAULTS, loadConfig, resolve, saveConfig } from "../src/oc/tui/config"
import { allThemes, parseTheme } from "../src/oc/tui/theme"
import { discoverThemes } from "../src/oc/tui/theme/discovery"
import { resolveThemeDocument, themeModes } from "@opencode/theme/tui"
import { blockOf, commandLine, editPatch, filetypeOf, inlineFromLabel, inlineOf } from "../src/tools.js"
import { rank, score } from "../src/ui/fuzzy.js"
import { abbreviateHome, compact, pickTip, TIPS } from "../src/util.js"
import type { Item } from "../../ink/state.js"

function scratch(): string {
  return mkdtempSync(path.join(os.tmpdir(), "kvist-opencode-pure-"))
}

describe("themes", () => {
  test("every theme OpenCode ships resolves in each mode it has", () => {
    // "system" is generated from the terminal's own palette; a file of that name (the core ships one) is not used.
    const names = Object.keys(allThemes()).filter((name) => name !== "system")
    expect(names).toContain("opencode")
    expect(names.length).toBeGreaterThan(30)
    for (const name of names) {
      const document = parseTheme(allThemes()[name]!, name)
      for (const mode of themeModes(document)) expect(() => resolveThemeDocument(document, mode)).not.toThrow()
    }
  })

  test("the themes Kvist's core ships load through the same pipeline", () => {
    const dir = path.join(import.meta.dirname, "..", "..", "..", "..", "kernel", "themes")
    for (const file of ["kvist.json"]) {
      const document = parseTheme(JSON.parse(readFileSync(path.join(dir, file), "utf8")), file)
      for (const mode of themeModes(document)) expect(() => resolveThemeDocument(document, mode)).not.toThrow()
    }
  })
})

describe("theme discovery", () => {
  test("a file called system.json is not loaded: it would shadow the theme generated from the terminal", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "kvist-opencode-themes-"))
    mkdirSync(path.join(dir, "themes"))
    writeFileSync(path.join(dir, "themes", "system.json"), "{}")
    writeFileSync(path.join(dir, "themes", "mine.json"), "{}")
    expect(Object.keys(await discoverThemes([dir]))).toEqual(["mine"])
  })
})

describe("the closed-session bar", () => {
  test("the button takes the place of the notice's own key hint and speaks its language", () => {
    expect(noticeWords("conversation over · Enter — new session")).toBe("conversation over")
    expect(noticeWords("разговор окончен · Enter — новая сессия")).toBe("разговор окончен")
    expect(noticeWords("just words")).toBe("just words")
    expect(buttonLabel("разговор окончен")).toBe("Новая сессия")
    expect(buttonLabel("conversation over")).toBe("New session")
  })
})

describe("settings", () => {
  test("defaults for a missing or damaged file", () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "kvist-opencode-cfg-"))
    expect(loadConfig(path.join(dir, "none.json"))).toEqual(DEFAULTS)
    writeFileSync(path.join(dir, "bad.json"), "{ not json")
    expect(loadConfig(path.join(dir, "bad.json"))).toEqual(DEFAULTS)
  })

  test("a saved choice is read back over the defaults and leaves no temporary file", () => {
    const file = path.join(mkdtempSync(path.join(os.tmpdir(), "kvist-opencode-cfg-")), "nested", "opencode-tui.json")
    saveConfig({ theme: { name: "dracula", mode: "light" }, session: { sidebar: "hide" } }, file)
    const loaded = loadConfig(file)
    expect(loaded.theme).toEqual({ name: "dracula", mode: "light" })
    expect(loaded.session).toEqual({ sidebar: "hide", scrollbar: false, thinking: "show" })
    expect(readFileSync(file, "utf8").endsWith("\n")).toBe(true)
  })

  test("the keys are OpenCode's own: leader, palette, agents", () => {
    const keys = resolve(DEFAULTS).keybinds
    expect(keys.get("command.palette.show")[0]?.key).toBe("ctrl+p")
    expect(keys.get("agent.cycle")[0]?.key).toBe("shift+tab")
    expect(keys.get("model.list")[0]?.key).toEqual(expect.stringContaining("leader"))
  })
})

describe("tools", () => {
  test("reads and searches are one-line rows", () => {
    expect(inlineOf("read_file", { path: "a.py" }).text).toBe("Read a.py")
    expect(inlineOf("read_file", { path: "a.py", start_line: 10, end_line: 20 }).text).toBe("Read a.py:10-20")
    expect(inlineOf("search_code", { query: "main", path: "src" }).text).toBe('Grep "main" in src')
    expect(inlineOf("find_files", { pattern: "*.ts" }).icon).toBe("✱")
    expect(inlineOf("fetch_url", { url: "https://x.dev" }).text).toBe("WebFetch https://x.dev")
  })

  test("an action with no entry still gets a row with its name and target", () => {
    expect(inlineOf("brand_new_action", { path: "p" }).text).toBe("brand_new_action p")
  })

  test("the reducer's explore labels turn back into rows", () => {
    expect(inlineFromLabel("read_multiple_files  b.py, c.py").text).toBe("Read b.py, c.py")
    expect(inlineFromLabel("search_code  needle").text).toBe('Grep "needle"')
    expect(inlineFromLabel("git_status").text).toBe("Git status")
  })

  test("only calls that carry content get a block", () => {
    expect(["run_shell", "write_file", "append_file", "replace_in_file"].map(blockOf)).toEqual(["shell", "write", "append", "edit"])
    expect(blockOf("read_file")).toBeNull()
  })

  test("a command is its argv joined", () => {
    expect(commandLine({ command: ["git", "status"] })).toBe("git status")
    expect(commandLine({ command: "ls" })).toBe("ls")
  })

  test("an edit becomes a unified patch with counted hunks", () => {
    const patch = editPatch("a.py", { old_text: "a\nb\nc", new_text: "a\nB\nc" }, 10)
    expect(patch).toBe("--- a/a.py\n+++ b/a.py\n@@ -10,3 +10,3 @@\n a\n-b\n+B\n c\n")
    expect(editPatch("a.py", { old_text: "same", new_text: "same" }, null)).toBe("")
  })

  test("only languages OpenTUI parses get a filetype", () => {
    expect(filetypeOf("src/a.tsx")).toBe("typescriptreact")
    expect(filetypeOf("README.md")).toBe("markdown")
    expect(filetypeOf("main.py")).toBeUndefined()
  })
})

describe("fuzzy search", () => {
  test("a prefix beats a substring beats scattered letters", () => {
    expect(score("gpt", "gpt-5")).toBeGreaterThan(score("gpt", "my-gpt"))
    expect(score("gpt", "my-gpt")).toBeGreaterThan(score("gpt", "g-p-t"))
    expect(score("xyz", "gpt-5")).toBe(0)
  })

  test("every word must match", () => {
    expect(rank(["fake-fast model", "fake-smart model"], "fake smart", (item) => item)).toEqual(["fake-smart model"])
    expect(rank(["b", "a"], "", (item) => item)).toEqual(["b", "a"])
  })
})

describe("prompt", () => {
  const commands = [{ name: "models" }, { name: "memory" }, { name: "new" }, { name: "themes" }]

  test("a slash with no space lists the commands that start with it, then ones that contain it", () => {
    expect(matchSlash("/me", commands).map((item) => item.name)).toEqual(["memory", "themes"])
    expect(matchSlash("/m", commands).map((item) => item.name)).toEqual(["models", "memory", "themes"])
    expect(matchSlash("/", commands)).toHaveLength(4)
  })

  test("nothing is listed for ordinary text or once an argument starts", () => {
    expect(matchSlash("fix it", commands)).toEqual([])
    expect(matchSlash("/models fast", commands)).toEqual([])
  })
})

describe("transcript layout", () => {
  const item = (kind: string, extra: Record<string, unknown> = {}) => ({ id: 1, kind, ...extra }) as unknown as Item

  test("the banner and the call line of the final answer are not drawn", () => {
    const items = [item("banner"), item("step", { action: "final_answer" }), item("step", { action: "read_file" }), item("answer", { text: "x" })]
    expect(visibleItems(items).map((shown) => shown.kind)).toEqual(["step", "answer"])
  })

  test("debug notes are drawn only when asked for, an error stays on the page, other notices are toasts", () => {
    const items = [
      item("note", { tone: "debug", text: "[native tools] supported" }),
      item("note", { tone: "info", text: "Role set to: coder" }),
      item("note", { tone: "warn", text: "Wait for the current run to finish." }),
      item("note", { tone: "error", text: "boom" }),
    ]
    expect(visibleItems(items).map((shown) => (shown as unknown as { text: string }).text)).toEqual(["boom"])
    expect(visibleItems(items, true)).toHaveLength(2)
  })

  test("rows that would draw nothing are left out so they leave no blank line", () => {
    const items = [item("thought", { text: " ", seconds: 0.2 }), item("thought", { text: "", seconds: 3 }), item("say", { text: "" }), item("answer", { text: "x" })]
    expect(visibleItems(items).map((shown) => shown.kind)).toEqual(["thought", "answer"])
  })

  test("changed files are the ones a successful write, append or edit touched, once each", () => {
    const tool = (action: string, path: string, ok = true) => item("tool", { action, args: { path }, result: { ok, path, summary: "", output: "", exit_code: null, first_line: null } })
    expect(changedFiles([tool("write_file", "a.py"), tool("replace_in_file", "a.py"), tool("append_file", "b.py"), tool("write_file", "c.py", false), tool("read_file", "d.py"), tool("run_shell", "e")])).toEqual(["a.py", "b.py"])
  })
})

describe("dialogs", () => {
  test("a session's age reads like OpenCode's", () => {
    const now = 1_000_000_000_000
    expect(ago(now / 1000 - 5, now)).toBe("just now")
    expect(ago(now / 1000 - 300, now)).toBe("5m ago")
    expect(ago(now / 1000 - 7200, now)).toBe("2h ago")
    expect(ago(now / 1000 - 3 * 86400, now)).toBe("3d ago")
  })

  test("empty sessions are not listed unless they are the open one", () => {
    const items = [
      { name: "a", title: "First", updated_at: 1, has_checkpoint: false, journal_bytes: 10 },
      { name: "empty", title: "", updated_at: 1, has_checkpoint: false, journal_bytes: 0 },
      { name: "open", title: "", updated_at: 1, has_checkpoint: false, journal_bytes: 0 },
    ]
    const options = sessionOptions(items, "open")
    expect(options.map((option) => option.value)).toEqual(["a", "open"])
    expect(options.map((option) => option.category)).toEqual(["Sessions", "Current"])
  })

})

describe("util", () => {
  test("the home folder is shortened", () => {
    expect(abbreviateHome("/home/u/work", "/home/u")).toBe("~/work")
    expect(abbreviateHome("/home/u", "/home/u")).toBe("~")
    expect(abbreviateHome("/srv/x", "/home/u")).toBe("/srv/x")
  })

  test("token counts are compact and tips come from the list", () => {
    expect([999, 1234, 2_500_000].map(compact)).toEqual(["999", "1.2K", "2.5M"])
    expect(TIPS as readonly string[]).toContain(pickTip(() => 0.5))
  })
})

describe("file mentions", () => {
  test("an @ that starts a word names the file being typed", () => {
    expect(mentionAt("look at @src/ap", 15)).toEqual({ start: 8, end: 15, query: "src/ap" })
    expect(mentionAt("@", 1)).toEqual({ start: 0, end: 1, query: "" })
    expect(mentionAt("mail me@host", 12)).toBeNull()
    expect(mentionAt("look at @a and more", 19)).toBeNull()
  })

  test("what the core refuses to read is not offered", () => {
    expect(["src/a.py", "docs/guide.md"].every(isOffered)).toBe(true)
    expect([".env", "app/.env.local", ".ssh/id_rsa", "certs/site.pem", "node_modules/x/index.js", ".git/config", ".kvist/sessions/a.json"].some(isOffered)).toBe(false)
  })
})
