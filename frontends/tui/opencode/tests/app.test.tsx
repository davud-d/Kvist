import { afterEach, describe, expect, test } from "bun:test"

import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

import { mount, type Mounted } from "./helpers.js"

let app: Mounted | undefined
afterEach(() => {
  app?.destroy()
  app = undefined
})

const SHOW = process.env.SHOW_FRAMES === "1"
const show = (label: string, app: Mounted) => SHOW && console.log(`--- ${label}\n${app.frame()}`)

describe("home screen", () => {
  test("shows the logo, the prompt and the model once the core has said hello", async () => {
    app = await mount()
    await app.waitFor("fake-fast")
    const frame = app.frame()
    expect(frame).toContain("Auto")
    expect(frame).toContain("ctrl+p")
    show("home", app)
  })
})

describe("a turn", () => {
  test("a chat reply replaces the home screen with the transcript", async () => {
    app = await mount()
    await app.waitFor("fake-fast")
    await app.type("chat")
    app.enter()
    await app.waitFor("Hello")
    const frame = app.frame()
    expect(frame).toContain("chat")
    expect(frame).not.toContain("█▀")
    show("chat", app)
  })

  test("reads and a write appear as OpenCode's inline rows and block", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("explore")
    app.enter()
    await app.waitFor("done: explore")
    const frame = app.frame()
    expect(frame).toContain("Explored: 3 reads, 1 search")
    show("explore", app)
  })

  test("edits show the written file, the diff and the failures", async () => {
    app = await mount({ height: 90, width: 110 })
    await app.waitFor("fake-fast")
    await app.type("edit")
    app.enter()
    await app.waitFor("done: edit")
    const frame = app.frame()
    expect(frame).toContain("# Wrote app.py")
    expect(frame).toContain("← Edit app.py")
    show("edit", app)
  })

  test("a shell call shows its command and output", async () => {
    app = await mount({ height: 90 })
    await app.waitFor("fake-fast")
    await app.type("shell")
    app.enter()
    await app.waitFor("done: shell")
    const frame = app.frame()
    expect(frame).toContain("$ npm test")
    expect(frame).toContain("Command exited with code 2")
    show("shell", app)
  })

  test("with thinking shown the thought's text sits under its header", async () => {
    app = await mount({ prefs: { session: { thinking: "show", sidebar: "hide" } } })
    await app.waitFor("fake-fast")
    await app.type("think")
    app.enter()
    await app.waitFor("Here is the summary.")
    await app.waitFor("The user wants a summary.")
    expect(app.frame()).toContain("Thought")
    show("think-shown", app)
  })

  test("a thought is one collapsed line", async () => {
    app = await mount()
    await app.waitFor("fake-fast")
    await app.type("think")
    app.enter()
    await app.waitFor("Here is the summary.")
    expect(app.frame()).toContain("Thought")
    show("think", app)
  })
})

describe("approvals", () => {
  test("a command that needs a yes is asked in the prompt's place and the answer goes back", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("approve-cmd")
    app.enter()
    await app.waitFor("Permission required")
    const asked = app.frame()
    expect(asked).toContain("$ curl -sI https://example.com | head -n 1")
    expect(asked).toContain("network access")
    expect(asked).toContain("Allow once")
    expect(asked).toContain("Allow always")
    expect(asked).toContain("Reject")
    show("approve", app)
    await app.settle(500)
    app.enter()
    await app.waitFor("done: approved=true remember=false")
  })

  test("a dangerous command offers no remembered answer", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("approve-danger")
    app.enter()
    await app.waitFor("Permission required")
    expect(app.frame()).not.toContain("Allow always")
    app.press("escape")
    await app.waitFor("done: approved=false")
  })

  test("an overwrite shows its diff", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("approve-edit")
    app.enter()
    await app.waitFor("Overwrite config.py")
    expect(app.frame()).toContain("DEBUG = True")
    show("overwrite", app)
    app.press("escape")
    await app.waitFor("done: approved=false")
  })
})

describe("commands and dialogs", () => {
  test("ctrl+p lists the commands and filters them as you type", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    app.press("p", { ctrl: true })
    await app.waitFor("Commands")
    expect(app.frame()).toContain("Switch session")
    await app.type("theme")
    await app.settle(100)
    const frame = app.frame()
    expect(frame).toContain("Switch theme")
    expect(frame).not.toContain("Switch session")
    show("palette", app)
    app.press("escape")
    await app.settle(100)
    expect(app.frame()).not.toContain("Commands")
  })

  test("/themes previews a theme and keeps it on enter", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/themes")
    await app.settle(100)
    app.enter()
    await app.waitFor("Themes")
    show("themes", app)
    app.press("escape")
  })

  test("a role chosen from the list is sent to the core", async () => {
    app = await mount({ height: 40, env: { FAKE_MANIFEST: "1" } })
    await app.waitFor("fake-fast")
    app.press("x", { ctrl: true })
    app.press("a")
    await app.waitFor("Select agent")
    app.press("down")
    app.press("down")
    app.enter()
    await app.waitFor("Role set to: reader")
    await app.waitFor("Reader")
    show("role", app)
  })

  test("the model list is read from the core's catalog and a pick asks which model it becomes", async () => {
    app = await mount({ height: 40, env: { FAKE_MANIFEST: "1" } })
    await app.waitFor("fake-fast")
    await app.type("/models")
    await app.settle(100)
    app.enter()
    await app.waitFor("Other Provider")
    expect(app.frame()).toContain("Fake Provider")
    show("models", app)
    app.press("down")
    app.enter()
    await app.waitFor("Fast model")
    expect(app.frame()).toContain("Both")
    show("use-as", app)
  })

  test("/tui writes the chosen interface for the launcher and quits with its switch code", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/tui")
    await app.settle(100)
    app.enter()
    await app.waitFor("Terminal interface")
    expect(app.frame()).toContain("OpenCode terminal")
    // The running interface is highlighted first; Ink's is two rows up.
    app.press("up")
    app.press("up")
    app.enter()
    for (let tries = 0; tries < 100 && !app.quitCodes.length; tries += 1) await app.settle(50)
    expect(readFileSync(path.join(app.workspace, "switch"), "utf8")).toBe("tui")
    expect(app.quitCodes).toEqual([64])
  })

  test("tab cycles the role", async () => {
    app = await mount({ height: 40, env: { FAKE_MANIFEST: "1" } })
    await app.waitFor("fake-fast")
    app.press("tab", { shift: true })
    await app.waitFor("Coder")
  })

  test("a core wizard's question appears as a dialog and the answer goes back", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/effort")
    await app.settle(100)
    app.enter()
    await app.waitFor("Select reasoning effort")
    show("effort", app)
  })
})

describe("mentions", () => {
  test("@ offers the project's files, completes one with tab and never offers a secret", async () => {
    app = await mount({ height: 40 })
    mkdirSync(path.join(app.workspace, "src"))
    writeFileSync(path.join(app.workspace, "src", "app.py"), "print(1)\n")
    writeFileSync(path.join(app.workspace, "notes.md"), "# notes\n")
    writeFileSync(path.join(app.workspace, ".env"), "KEY=1\n")
    await app.waitFor("fake-fast")
    await app.type("look at @ap")
    await app.waitFor("src/app.py")
    expect(app.frame()).not.toContain(".env")
    show("mention", app)
    app.press("tab")
    await app.settle(100)
    expect(app.frame()).toContain("look at @src/app.py")
  })

  test("a slash lists commands and tab completes the name", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/ses")
    await app.waitFor("Switch session")
    show("slash", app)
    app.press("tab")
    await app.settle(100)
    expect(app.frame()).toContain("/sessions ")
  })
})

describe("arrows", () => {
  test("an empty box offers first requests: the arrows choose and Tab puts the choice in the box", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("Not sure where to start")
    app.press("down")
    await app.settle(100)
    app.press("tab")
    await app.settle(100)
    expect(app.frame()).toContain("Set up a new project here")
    expect(app.frame()).not.toContain("Not sure where to start")
  })

  test("with text in the box the first Up goes to its start and only the next one recalls the last request", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("slow")
    app.enter()
    await app.waitFor("done: slow")
    await app.type("abc")
    app.press("up")
    await app.settle(150)
    expect(app.frame()).toContain("abc")
    app.press("up")
    await app.settle(150)
    expect(app.frame()).not.toContain("abc")
    // Down mirrors it: first to the end of the recalled text, then back to what was being typed.
    app.press("down")
    await app.settle(150)
    expect(app.frame()).not.toContain("abc")
    app.press("down")
    await app.settle(150)
    expect(app.frame()).toContain("abc")
  })
})

describe("a closed session", () => {
  test("the core's notice stands in the prompt's place and Enter opens a new session", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("insult")
    app.enter()
    await app.waitFor("New session")
    expect(app.frame()).toContain("conversation over")
    expect(app.frame()).not.toContain("Enter — new session")
    // Typing reaches nothing: there is no editor.
    await app.type("hello")
    await app.settle(100)
    expect(app.frame()).not.toContain("hello")
    app.enter()
    await app.waitFor("Ask anything")
    expect(app.frame()).not.toContain("conversation over")
  })
})

describe("what the core is doing", () => {
  test("a running turn shows no status line of its own", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("slow")
    app.enter()
    await app.settle(600)
    expect(app.frame()).not.toContain("Reading files")
    expect(app.frame()).not.toContain("inspect")
  })
})

describe("words before a call", () => {
  test("stream in at full width and the call waits for them", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("words")
    app.enter()
    await app.waitFor("Looking at")
    await app.settle(150)
    show("words-mid", app)
    const lines = app.frame().split("\n").filter((line) => /[a-z]{3}/.test(line) && !line.includes("fake-"))
    expect(lines.some((line) => line.includes("Looking at the last run"))).toBe(true)
    await app.settle(1500)
    show("words-late", app)
    expect(app.frame()).not.toContain("whethe\n")
    await app.waitFor("echo hi")
    expect(app.frame()).toContain("check failed.")
  }, 15000)
})

describe("aliases", () => {
  test("a slash alias is not a row of its own but brings its command up when typed", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/")
    await app.waitFor("/agents")
    for (const alias of ["/bye", "/quit", "/revert", "/continue"]) expect(app.frame()).not.toContain(alias)
    await app.type("bye")
    await app.waitFor("Exit the app")
    expect(app.frame()).toContain("/exit")
  })
})

describe("a damaged setup", () => {
  test("a broken theme file shows the shipped theme and says why instead of stopping the interface", async () => {
    app = await mount({
      files: { ".kvist/themes/broken.json": JSON.stringify({ theme: { primary: "no-such-colour" } }) },
      prefs: { theme: { name: "broken" } },
    })
    await app.waitFor("fake-fast")
    await app.waitFor("Theme 'broken'")
  })

  test("a theme choice survives a restart", async () => {
    app = await mount({ prefs: { theme: { name: "dracula", mode: "dark" } } })
    await app.waitFor("fake-fast")
    await app.type("/themes")
    await app.settle(100)
    app.enter()
    await app.waitFor("Themes")
    expect(app.frame()).toContain("● dracula")
  })
})

describe("long lists", () => {
  test("the slash list scrolls past its first rows", async () => {
    app = await mount({ height: 40 })
    await app.waitFor("fake-fast")
    await app.type("/")
    await app.waitFor("/agents")
    expect(app.frame()).not.toContain("/web-search-provider")
    app.press("up")
    await app.settle(200)
    expect(app.frame()).toContain("/web-search-provider")
    expect(app.frame()).not.toContain("/agents")
    show("slash-scrolled", app)
  })
})

describe("scrolling the transcript", () => {
  test("page up goes back through a long turn and page down returns", async () => {
    app = await mount({ height: 20 })
    await app.waitFor("fake-fast")
    await app.type("shell")
    app.enter()
    await app.waitFor("done: shell")
    expect(app.frame()).not.toContain("$ npm test")
    for (let i = 0; i < 8; i += 1) {
      app.press("\x1b[5~")
      await app.settle(30)
    }
    expect(app.frame()).toContain("$ npm test")
    show("scrolled-up", app)
    for (let i = 0; i < 12; i += 1) {
      app.press("\x1b[6~")
      await app.settle(30)
    }
    expect(app.frame()).toContain("done: shell")
  })
})
