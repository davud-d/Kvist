import assert from "node:assert/strict"
import { test } from "node:test"

import { starterGroups } from "./homeStarters"
import { ago } from "./sessions"

for (const russian of [false, true]) {
  test(`every starter has a title, a hint and a prompt (russian=${russian})`, () => {
    const groups = starterGroups(russian)
    assert.equal(groups.length, 4)
    for (const group of groups) {
      assert.equal(group.starters.length, 3)
      for (const starter of group.starters) {
        assert.ok(starter.title && starter.hint && starter.prompt.trim())
      }
    }
  })
}

for (const russian of [false, true]) {
  test(`no starter leaves a hole for the person to fill (russian=${russian})`, () => {
    for (const group of starterGroups(russian)) {
      for (const starter of group.starters) {
        assert.doesNotMatch(starter.prompt, /: ?$/)
        assert.doesNotMatch(starter.prompt, /: \./)
      }
    }
  })
}

test("the document starters ask for the source of every figure", () => {
  const collect = starterGroups(false).find((group) => group.id === "documents")!.starters[0]
  assert.match(collect.prompt, /file it came from/)
})

test("ago speaks Russian with the right noun forms", () => {
  const now = 100_000
  assert.equal(ago(now - 30, now, true), "только что")
  assert.equal(ago(now - 60, now, true), "1 минуту назад")
  assert.equal(ago(now - 180, now, true), "3 минуты назад")
  assert.equal(ago(now - 60 * 11, now, true), "11 минут назад")
  assert.equal(ago(now - 3600 * 21, now, true), "21 час назад")
  assert.equal(ago(now - 86400 * 2, now, true), "2 дня назад")
  assert.equal(ago(now - 60, now), "1 min ago")
})
