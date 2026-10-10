import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { test } from "node:test"

import { DRIVES, breadthOf, crumbsOf, initialFolder, isRoot, joinPath, parentOf, rootOf, rowsOf, tildePath, updateFolder, visibleDirs, type FolderAction, type FolderState } from "../setup/folders.js"
import { initialState, update, type SetupState } from "../setup/flow.js"
import type { InputEvent } from "../setup/keys.js"
import { listDirectory, listDrives, makeDirectory } from "../setup/listing.js"
import { renderScene } from "../setup/scene.js"
import { stringsFor } from "../setup/strings.js"

const strings = stringsFor("en")
const plain = (text: string) => text.replace(/\x1b\[[0-9;]*m/g, "")

const dirs = (...names: string[]) => names.map((name) => ({ name, git: name === "app" }))
const loaded = (path = "/home/me/work"): FolderState => ({
  ...initialFolder(path, "/home/me"),
  status: "ready",
  dirs: dirs(".cache", "app", "docs", "notes"),
  files: 3,
})
const key = (name: string, text = "", ctrl = false): FolderAction => ({ type: "input", event: { type: "key", key: name, text, ctrl } as InputEvent })
const typed = (state: FolderState, text: string) => [...text].reduce((s, ch) => updateFolder(s, key("char", ch)).state, state)

test("paths: parent, join and the trail with the home folder as ~", () => {
  assert.equal(parentOf("/home/me/work"), "/home/me")
  assert.equal(parentOf("/home"), "/")
  assert.equal(parentOf("/"), "/")
  assert.equal(joinPath("/", "etc"), "/etc")
  assert.equal(joinPath("/a", "b"), "/a/b")
  assert.deepEqual(crumbsOf("/home/me/work/app", "/home/me").map((crumb) => crumb.label), ["~", "work", "app"])
  assert.deepEqual(crumbsOf("/mnt/data", "/home/me").map((crumb) => crumb.label), ["/", "mnt", "data"])
  assert.equal(crumbsOf("/home/me", "/home/me").length, 1)
})

test("rows: use this folder, the parent, then folders; hidden ones only when asked for", () => {
  const state = loaded()
  assert.deepEqual(rowsOf(state).map((row) => row.kind), ["use", "parent", "dir", "dir", "dir"])
  assert.deepEqual(visibleDirs(state).map((entry) => entry.name), ["app", "docs", "notes"])
  const shown = updateFolder(state, key("char", "d", true)).state
  assert.equal(shown.showHidden, true)
  assert.deepEqual(visibleDirs(shown).map((entry) => entry.name), [".cache", "app", "docs", "notes"])
  assert.deepEqual(rowsOf({ ...state, path: "/" }).map((row) => row.kind), ["use", "dir", "dir", "dir"])
})

test("Enter on the first row chooses the folder; on a folder it goes in; Backspace goes up", () => {
  const state = loaded()
  assert.deepEqual(updateFolder(state, key("enter")).effect, { type: "choose", path: "/home/me/work" })
  const down = updateFolder(updateFolder(updateFolder(state, key("down")).state, key("down")).state, key("enter"))
  assert.deepEqual(down.effect, { type: "list", path: "/home/me/work/app" })
  assert.equal(down.state.status, "loading")
  const up = updateFolder(state, key("backspace"))
  assert.deepEqual(up.effect, { type: "list", path: "/home/me" })
  assert.equal(updateFolder({ ...state, path: "/" }, key("left")).effect, undefined)
})

test("typing filters the folders; Backspace and Esc take the filter back before leaving", () => {
  let state = typed(loaded(), "do")
  assert.deepEqual(visibleDirs(state).map((entry) => entry.name), ["docs"])
  assert.equal(updateFolder(state, key("backspace")).state.filter, "d")
  assert.equal(updateFolder(state, key("escape")).state.filter, "")
  assert.equal(updateFolder(state, key("escape")).effect, undefined)
  state = updateFolder(state, key("escape")).state
  assert.deepEqual(updateFolder(state, key("escape")).effect, { type: "leave" })
})

test("a new folder is named, made, and entered", () => {
  let state = updateFolder(loaded(), key("char", "n", true)).state
  assert.equal(state.creating, "")
  assert.equal(updateFolder(state, key("enter")).effect, undefined)
  state = typed(state, "proj")
  assert.deepEqual(updateFolder(state, key("enter")).effect, { type: "make", parent: "/home/me/work", name: "proj" })
  const failed = updateFolder(state, { type: "made", path: "/home/me/work/proj", error: "A file or folder with this name already exists." })
  assert.equal(failed.state.creating, "proj")
  const made = updateFolder(state, { type: "made", path: "/home/me/work/proj" })
  assert.deepEqual(made.effect, { type: "list", path: "/home/me/work/proj" })
  assert.equal(made.state.creating, null)
  assert.equal(updateFolder(state, key("escape")).state.creating, null)
})

test("clicks: trail, places, toggles and buttons", () => {
  const state = loaded()
  assert.deepEqual(updateFolder(state, { type: "click", target: { kind: "crumb", index: 0 } }).effect, { type: "list", path: "/home/me" })
  assert.deepEqual(updateFolder(state, { type: "click", target: { kind: "place", id: "root" } }).effect, { type: "list", path: "/" })
  assert.equal(updateFolder(state, { type: "click", target: { kind: "toggle", id: "hidden" } }).state.showHidden, true)
  assert.deepEqual(updateFolder(state, { type: "click", target: { kind: "action", id: "use" } }).effect, { type: "choose", path: "/home/me/work" })
  assert.deepEqual(updateFolder(state, { type: "click", target: { kind: "row", index: 2 } }).effect, { type: "list", path: "/home/me/work/app" })
})

test("a listing for a folder that was left changes nothing", () => {
  const state = loaded()
  const stale = updateFolder(state, { type: "listing", listing: { path: "/elsewhere", dirs: dirs("x"), files: 0 } })
  assert.deepEqual(stale.state.dirs, state.dirs)
  const failed = updateFolder(state, { type: "listing", listing: { path: state.path, dirs: [], files: 0, error: "No permission to read this folder." } })
  assert.deepEqual([failed.state.status, failed.state.error], ["failed", "No permission to read this folder."])
})

test("the welcome screen opens the chooser on F and takes the chosen folder back", () => {
  const start: SetupState = { ...initialState("/home/me/work", "/home/me"), catalog: "ready" }
  const opened = update(start, { type: "input", event: { type: "key", key: "char", text: "f", ctrl: false } }, strings)
  assert.equal(opened.state.step, "folder")
  assert.deepEqual(opened.effect, { type: "list", path: "/home/me/work" })
  const listed = update(opened.state, { type: "listing", listing: { path: "/home/me/work", dirs: dirs("app"), files: 1 } }, strings)
  const chosen = update(listed.state, { type: "input", event: { type: "key", key: "enter", text: "", ctrl: false } }, strings)
  assert.deepEqual([chosen.state.step, chosen.state.workspace, chosen.state.folder], ["welcome", "/home/me/work", null])
  const cancelled = update(opened.state, { type: "input", event: { type: "key", key: "escape", text: "", ctrl: false } }, strings)
  assert.deepEqual([cancelled.state.step, cancelled.state.workspace], ["welcome", "/home/me/work"])
  // Ctrl+C still quits from inside the chooser.
  const quit = update(opened.state, { type: "input", event: { type: "key", key: "char", text: "c", ctrl: true } }, strings)
  assert.deepEqual(quit.effect, { type: "finish", outcome: { kind: "quit" } })
})

test("listing a real folder: folders first and sorted, .git marked, files counted", async () => {
  const root = mkdtempSync(join(tmpdir(), "kvist-folders-"))
  mkdirSync(join(root, "beta"))
  mkdirSync(join(root, "Alpha"))
  mkdirSync(join(root, "proj", ".git"), { recursive: true })
  writeFileSync(join(root, "readme.md"), "")
  writeFileSync(join(root, "b.txt"), "")
  const listing = await listDirectory(root)
  assert.deepEqual(listing.dirs.map((entry) => [entry.name, entry.git]), [["Alpha", false], ["beta", false], ["proj", true]])
  assert.equal(listing.files, 2)
  const missing = await listDirectory(join(root, "nope"))
  assert.match(missing.error ?? "", /no longer exists/)
})

test("making a folder: made, refused when it exists, refused with a slash", async () => {
  const root = mkdtempSync(join(tmpdir(), "kvist-make-"))
  assert.deepEqual(await makeDirectory(root, "new"), { path: join(root, "new") })
  assert.match((await makeDirectory(root, "new")).error ?? "", /already exists/)
  assert.match((await makeDirectory(root, "a/b")).error ?? "", /slash/)
  assert.match((await makeDirectory(root, "..")).error ?? "", /slash/)
})

test("the chooser fills the window, shows the trail, the places and the rows, and fits 80x24", () => {
  const folder = loaded()
  const state: SetupState = { ...initialState("/home/me/work", "/home/me"), step: "folder", folder, catalog: "ready" }
  for (const [cols, rows] of [[120, 36], [80, 24]] as const) {
    const scene = renderScene({ state, strings, cols, rows, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "~/work", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
    const text = scene.rows.map(plain).join("\n")
    assert.match(text, /Choose the working folder/)
    assert.match(text, /~ › work/)
    assert.match(text, /Use this folder/)
    assert.match(text, /docs/)
    assert.match(text, /3 files/)
    assert.ok(scene.panel && scene.panel.glass && scene.panel.height >= rows - 5)
    for (const row of scene.rows) assert.equal([...plain(row)].length, cols)
    const targets = scene.hits.map((hit) => hit.target)
    assert.ok(targets.some((target) => target.kind === "folder" && target.target.kind === "row"))
    assert.ok(targets.some((target) => target.kind === "folder" && target.target.kind === "crumb"))
    assert.ok(targets.some((target) => target.kind === "folder" && target.target.kind === "action" && target.target.id === "use"))
  }
})

test("a ghost draws only an empty pane of the given size", () => {
  const ghost = { col: 10, row: 5, width: 40, height: 10 }
  const scene = renderScene({ state: initialState("/x", "/h"), strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost, picture: "cells" })
  assert.deepEqual([scene.panel?.col, scene.panel?.row, scene.panel?.width, scene.panel?.height], [10, 5, 40, 10])
  assert.ok(!scene.rows.map(plain).join("\n").includes("Welcome"))
  assert.equal(scene.hits.length, 0)
})

test("Windows paths: drive roots, backslashes, ~ for the profile folder, no parent above a drive", () => {
  assert.equal(parentOf("C:\\Users\\me\\work"), "C:\\Users\\me")
  assert.equal(parentOf("C:\\Users"), "C:\\")
  assert.equal(parentOf("C:\\"), "C:\\")
  assert.equal(joinPath("C:\\", "src"), "C:\\src")
  assert.equal(joinPath("C:\\Users\\me", "work"), "C:\\Users\\me\\work")
  assert.equal(rootOf("D:\\data\\x"), "D:\\")
  assert.equal(isRoot("C:\\"), true)
  assert.equal(isRoot("C:\\Users"), false)
  assert.deepEqual(crumbsOf("C:\\Users\\me\\work", "C:\\Users\\me").map((c) => c.label), ["~", "work"])
  assert.deepEqual(crumbsOf("D:\\data\\x", "C:\\Users\\me").map((c) => [c.label, c.path]), [["D:\\", "D:\\"], ["data", "D:\\data"], ["x", "D:\\data\\x"]])
  // The profile folder is found whatever the case of the drive letter and folders.
  assert.equal(tildePath("c:\\users\\ME\\work", "C:\\Users\\me"), "~\\work")
  assert.equal(tildePath("C:\\Users\\meow", "C:\\Users\\me"), "C:\\Users\\meow")
  const atRoot = { ...initialFolder("C:\\", "C:\\Users\\me"), status: "ready" as const }
  assert.deepEqual(rowsOf(atRoot).map((row) => row.kind), ["use"])
  assert.equal(updateFolder(atRoot, key("left")).effect, undefined)
  assert.deepEqual(updateFolder({ ...atRoot, path: "C:\\Users" }, { type: "click", target: { kind: "place", id: "root" } }).effect, { type: "list", path: "C:\\" })
})

test("drives: the parent of a drive's root is the list of drives, and entering one lands in its root", () => {
  const atRoot = { ...initialFolder("C:\\", "C:\\Users\\me", true), status: "ready" as const, dirs: dirs("Windows") }
  assert.deepEqual(rowsOf(atRoot).map((row) => row.kind), ["use", "parent", "dir"])
  const up = updateFolder({ ...atRoot, index: 1 }, key("enter"))
  assert.deepEqual(up.effect, { type: "list", path: DRIVES })
  assert.equal(up.state.path, DRIVES)
  assert.equal(updateFolder(atRoot, key("left")).effect?.type, "list")
  // Among the drives: one row each, no "use", no parent, and nothing is chosen from here.
  const view = updateFolder(up.state, { type: "listing", listing: { path: DRIVES, dirs: dirs("C:\\", "D:\\", "E:\\"), files: 0 } }).state
  assert.deepEqual(rowsOf(view).map((row) => row.kind), ["dir", "dir", "dir"])
  assert.equal(updateFolder(view, { type: "click", target: { kind: "action", id: "use" } }).effect, undefined)
  assert.equal(updateFolder(view, key("left")).effect, undefined)
  assert.deepEqual(crumbsOf(DRIVES, "C:\\Users\\me").map((crumb) => crumb.path), [DRIVES])
  const entered = updateFolder({ ...view, index: 1 }, key("enter"))
  assert.deepEqual(entered.effect, { type: "list", path: "D:\\" })
  // The root chip means the root of the drive the chooser was started on.
  assert.deepEqual(updateFolder(view, { type: "click", target: { kind: "place", id: "root" } }).effect, { type: "list", path: "C:\\" })
  assert.deepEqual(updateFolder(loaded(), { type: "click", target: { kind: "place", id: "drives" } }).effect, undefined)
  assert.deepEqual(updateFolder(atRoot, { type: "click", target: { kind: "place", id: "drives" } }).effect, { type: "list", path: DRIVES })
})

test("a machine without drive letters has no level above the root", () => {
  const unix = { ...initialFolder("/", "/home/me"), status: "ready" as const, dirs: dirs("etc") }
  assert.deepEqual(rowsOf(unix).map((row) => row.kind), ["use", "dir"])
  assert.equal(updateFolder(unix, key("left")).effect, undefined)
})

test("listing drives: those that answer, in letter order; one that hangs is left out", async () => {
  const answering = new Set(["C:\\", "D:\\", "F:\\"])
  const check = (root: string) => (root === "E:\\" ? new Promise<boolean>(() => undefined) : Promise.resolve(answering.has(root)))
  const listing = await listDrives(check)
  assert.equal(listing.path, DRIVES)
  assert.deepEqual(listing.dirs.map((entry) => entry.name), ["C:\\", "D:\\", "F:\\"])
})

test("the drives view shows the drives, marks the current one and offers only Cancel", () => {
  const folder = {
    ...initialFolder("D:\\code", "C:\\Users\\me", true),
    path: DRIVES,
    status: "ready" as const,
    dirs: dirs("C:\\", "D:\\"),
  }
  const state: SetupState = { ...initialState("D:\\code", "C:\\Users\\me", true), step: "folder", folder, catalog: "ready" }
  const scene = renderScene({ state, strings, cols: 100, rows: 30, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost: null, picture: "cells" })
  const text = scene.rows.map(plain).join("\n")
  assert.match(text, /C:\\/)
  assert.match(text, /D:\\\s+current/)
  assert.match(text, /Choose a drive to look into/)
  assert.doesNotMatch(text, /Use this folder/)
  assert.match(text, /Cancel/)
})

test("a folder is too wide when it is home, the top of a disk or where everything lands", () => {
  assert.equal(breadthOf("/home/me", "/home/me"), "home")
  assert.equal(breadthOf("/home/me/", "/home/me"), "home")
  assert.equal(breadthOf("/", "/home/me"), "root")
  assert.equal(breadthOf(DRIVES, "/home/me"), "root")
  assert.equal(breadthOf("/home", "/home/me"), "crowded")
  assert.equal(breadthOf("/home/me/Downloads", "/home/me"), "crowded")
  assert.equal(breadthOf("/home/me/documents", "/home/me"), "crowded")
  // One project, or something deeper inside the places that gather files, is not wide.
  assert.equal(breadthOf("/home/me/work/kvist", "/home/me"), null)
  assert.equal(breadthOf("/home/me/Documents/app", "/home/me"), null)
  assert.equal(breadthOf("/srv/app", "/home/me"), null)
  // Nothing is known about an unset folder, so nothing is warned about.
  assert.equal(breadthOf("", ""), null)
  // The same readings for Windows paths, whatever the letter case.
  assert.equal(breadthOf("C:\\Users\\Me", "c:\\users\\me"), "home")
  assert.equal(breadthOf("D:\\", "C:\\Users\\Me"), "root")
  assert.equal(breadthOf("C:\\Users", "C:\\Users\\Me"), "crowded")
  assert.equal(breadthOf("C:\\Users\\Me\\Desktop", "C:\\Users\\Me"), "crowded")
  assert.equal(breadthOf("C:\\Users\\Me\\src\\app", "C:\\Users\\Me"), null)
})
