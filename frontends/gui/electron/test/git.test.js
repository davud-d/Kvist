"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const { execFileSync } = require("node:child_process")
const test = require("node:test")

const git = require("../src/git")

process.env.GIT_AUTHOR_NAME = "Test"
process.env.GIT_AUTHOR_EMAIL = "test@example.invalid"
process.env.GIT_COMMITTER_NAME = "Test"
process.env.GIT_COMMITTER_EMAIL = "test@example.invalid"

function makeRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-git-"))
  execFileSync("git", ["init", "-q"], { cwd: dir })
  return dir
}

test("parseStatus reads untracked, modified and renamed entries", () => {
  const raw = ["?? new.txt", " M a.py", "R  new name.py", "old name.py", ""].join("\0")
  assert.deepEqual(git.parseStatus(raw).map((entry) => [entry.path, entry.untracked]), [
    ["new.txt", true],
    ["a.py", false],
    ["new name.py", false],
  ])
})

test("newFilePatch is a parseable all-added patch", () => {
  const patch = git.newFilePatch("a/b.txt", "one\ntwo\n")
  assert.match(patch, /^diff --git a\/a\/b\.txt b\/a\/b\.txt\n/)
  assert.match(patch, /@@ -0,0 \+1,2 @@/)
  assert.match(patch, /\n\+one\n\+two\n/)
})

test("checkedRelativePaths refuses escapes and option-looking names", () => {
  const root = path.join(os.tmpdir(), "kvist-root")
  assert.deepEqual(git.checkedRelativePaths(root, ["a.py", "dir/b.py"]), ["a.py", path.join("dir", "b.py")])
  assert.throws(() => git.checkedRelativePaths(root, ["../outside"]), /inside the workspace/)
  assert.throws(() => git.checkedRelativePaths(root, ["-rf"]), /inside the workspace/)
  assert.throws(() => git.checkedRelativePaths(root, []), /at least one/)
})

test("splitPatchByFile gives one unified diff per file", () => {
  const parts = git.splitPatchByFile("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/y b/y\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-c\n+d\n")
  assert.deepEqual(parts.map((part) => part.path), ["x", "y"])
  assert.match(parts[1].patch, /\+d/)
})

test("not a repository", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kvist-nogit-"))
  assert.deepEqual(await git.gitInfo(dir), { isRepo: false })
  assert.deepEqual(await git.gitDiff(dir), { isRepo: false, patch: "" })
})

test("a repository with no commits shows new files and never .kvist", async () => {
  const dir = makeRepo()
  fs.writeFileSync(path.join(dir, "hello.py"), "print('hi')\n")
  fs.mkdirSync(path.join(dir, ".kvist"))
  fs.writeFileSync(path.join(dir, ".kvist", "state.json"), "{}")

  const info = await git.gitInfo(dir)
  assert.equal(info.isRepo, true)
  assert.deepEqual(info.files.map((file) => file.path), ["hello.py"])

  const diff = await git.gitDiff(dir)
  assert.match(diff.patch, /\+print\('hi'\)/)
  assert.doesNotMatch(diff.patch, /\.kvist/)
})

test("commit takes only the chosen files", async () => {
  const dir = makeRepo()
  fs.writeFileSync(path.join(dir, "keep.txt"), "keep\n")
  fs.writeFileSync(path.join(dir, "skip.txt"), "skip\n")
  const result = await git.gitCommit(dir, { message: "Add keep", paths: ["keep.txt"] })
  assert.match(result.commit, /Add keep$/)
  const tracked = execFileSync("git", ["ls-files"], { cwd: dir, encoding: "utf8" }).trim()
  assert.equal(tracked, "keep.txt")

  const info = await git.gitInfo(dir)
  assert.deepEqual(info.files.map((file) => file.path), ["skip.txt"])
})

test("an empty message or an escaping path is refused before git runs", async () => {
  const dir = makeRepo()
  await assert.rejects(git.gitCommit(dir, { message: "  ", paths: ["a"] }), /empty/)
  await assert.rejects(git.gitCommit(dir, { message: "x", paths: ["../a"] }), /inside the workspace/)
})

test("modified tracked files count lines against HEAD", async () => {
  const dir = makeRepo()
  fs.writeFileSync(path.join(dir, "a.txt"), "one\n")
  await git.gitCommit(dir, { message: "init", paths: ["a.txt"] })
  fs.writeFileSync(path.join(dir, "a.txt"), "one\ntwo\nthree\n")
  const info = await git.gitInfo(dir)
  assert.equal(info.added, 2)
  assert.equal(info.removed, 0)
})
