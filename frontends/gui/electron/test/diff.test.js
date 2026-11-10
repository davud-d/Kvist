"use strict"

const assert = require("node:assert/strict")
const test = require("node:test")

const { parseUnifiedDiff } = require("../renderer/js/diff.js")

const GIT_DIFF = [
  "diff --git a/src/a.py b/src/a.py",
  "index 111..222 100644",
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,3 +1,4 @@",
  " keep",
  "-old",
  "+new",
  "+extra",
  " tail",
  "diff --git a/new.txt b/new.txt",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/new.txt",
  "@@ -0,0 +1 @@",
  "+hello",
  "diff --git a/gone.txt b/gone.txt",
  "deleted file mode 100644",
  "--- a/gone.txt",
  "+++ /dev/null",
  "@@ -1 +0,0 @@",
  "-bye",
  "diff --git a/img.png b/img.png",
  "Binary files a/img.png and b/img.png differ",
  "",
].join("\n")

test("parses a multi-file git diff with line numbers and counts", () => {
  const files = parseUnifiedDiff(GIT_DIFF)
  assert.deepEqual(files.map((file) => [file.path, file.status, file.added, file.removed]), [
    ["src/a.py", "modified", 2, 1],
    ["new.txt", "added", 1, 0],
    ["gone.txt", "deleted", 0, 1],
    ["img.png", "modified", 0, 0],
  ])
  const lines = files[0].hunks[0].lines
  assert.deepEqual(lines.map((line) => [line.type, line.oldNo ?? null, line.newNo ?? null]), [
    ["ctx", 1, 1],
    ["del", 2, null],
    ["add", null, 2],
    ["add", null, 3],
    ["ctx", 3, 4],
  ])
  assert.equal(files[3].binary, true)
})

test("parses the bare single-file patch Kvist attaches to a result", () => {
  const files = parseUnifiedDiff("--- a/x.py\n+++ b/x.py\n@@ -5 +5 @@\n-a\n+b\n")
  assert.equal(files.length, 1)
  assert.equal(files[0].path, "x.py")
  assert.equal(files[0].hunks[0].lines[0].oldNo, 5)
  assert.equal(files[0].hunks[0].lines[1].newNo, 5)
})

test("a created file from /dev/null is added", () => {
  const files = parseUnifiedDiff("--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n")
  assert.equal(files[0].status, "added")
  assert.equal(files[0].path, "n.py")
  assert.equal(files[0].added, 2)
})

test("a rename keeps the old path", () => {
  const files = parseUnifiedDiff("diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-a\n+b\n")
  assert.equal(files[0].status, "renamed")
  assert.equal(files[0].oldPath, "old.py")
  assert.equal(files[0].path, "new.py")
})

test("a no-newline marker is kept as a meta line, not counted", () => {
  const files = parseUnifiedDiff("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n")
  assert.equal(files[0].added, 1)
  assert.equal(files[0].removed, 1)
  assert.ok(files[0].hunks[0].lines.some((line) => line.type === "meta"))
})

test("empty and junk input give no files", () => {
  assert.deepEqual(parseUnifiedDiff(""), [])
  assert.deepEqual(parseUnifiedDiff(null), [])
  assert.deepEqual(parseUnifiedDiff("hello\nworld"), [])
})
