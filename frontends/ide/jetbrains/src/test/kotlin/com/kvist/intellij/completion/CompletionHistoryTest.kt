package com.kvist.intellij.completion

import com.google.gson.JsonParser
import org.junit.Assert.*
import org.junit.Test

class CompletionHistoryTest {
    private val scope = listOf("binary", "project")
    private val anchor = CompletionPayload("a.py", "python", "pri", "")

    @Test fun `typing consumes the suggestion and backspace restores it after garbage`() {
        val history = CompletionHistory()
        history.set(scope, anchor, Completion("nt(foo)"))
        assertEquals("t(foo)", history.get(scope, anchor.copy(prefix = "prin"))?.text)
        assertNull(history.get(scope, anchor.copy(prefix = "print(foo)")))
        assertNull(history.get(scope, anchor.copy(prefix = "prixxx")))
        assertEquals("nt(foo)", history.get(scope, anchor)?.text)
        assertNull(history.get(listOf("binary", "another project"), anchor))
        assertNull(history.get(scope, anchor.copy(suffix = "changed")))
        assertNull(history.get(scope, anchor.copy(language = "javascript")))
    }

    @Test fun `full context and bounded recency prevent unrelated hits`() {
        val history = CompletionHistory(2)
        val a = anchor.copy(prefix = "import a\n" + "x".repeat(100))
        val b = a.copy(prefix = "import b\n" + "x".repeat(100))
        history.set(scope, a, Completion("one"))
        assertNull(history.get(scope, b))
        history.set(scope, b, Completion("two"))
        history.get(scope, a)
        history.set(scope, anchor, Completion(""))
        assertNull(history.get(scope, b))
        assertEquals("one", history.get(scope, a)?.text)
        assertEquals("", history.get(scope, anchor)?.text)
    }

    @Test fun `a replacement is reused only at its exact anchor`() {
        val history = CompletionHistory()
        val edit = Completion("cache", before = 3)
        history.set(scope, anchor, edit)
        assertEquals(edit, history.get(scope, anchor))
        assertNull(history.get(scope, anchor.copy(prefix = anchor.prefix + "c")))
    }

    @Test fun `a replacement reply needs a text and whole offsets`() {
        fun parsed(json: String) = parseReplacement(JsonParser.parseString(json).asJsonObject)
        assertEquals(Completion("cache\n.gradle", 3, 0), parsed("""{"text":"cache\n.gradle","before":3,"after":0}"""))
        assertNull(parsed("""{"text":"x","before":0,"after":0}"""))
        assertEquals(Completion("x", -1, 7), parsed("""{"text":"x","before":-1,"after":7}"""))
        assertNull(parsed("""{"text":"x","before":1.5,"after":0}"""))
        assertNull(parsed("""{"text":7,"before":1,"after":0}"""))
        assertNull(parsed("""{"before":1,"after":0}"""))
    }

    @Test fun `server lines are replies only when they carry a numeric id`() {
        assertEquals("ret", parseServerLine("""{"id":3,"delta":"ret"}""")!!.get("delta").asString)
        assertTrue(parseServerLine("""{"id":3,"done":true}""")!!.get("done").asBoolean)
        assertNull(parseServerLine("a stray warning"))
        assertNull(parseServerLine("""{"delta":"x"}"""))
        assertNull(parseServerLine("""{"id":"3"}"""))
        assertNull(parseServerLine("[1, 2]"))
    }

    @Test fun `request carries the id and the buffer around the caret`() {
        val json = anchor.toRequest(9)
        assertEquals(9L, json.get("id").asLong)
        assertEquals("pri", json.get("prefix").asString)
        assertEquals("a.py", json.get("file_path").asString)
        assertTrue(json.get("edits").asBoolean)
    }
}
