package com.kvist.intellij.completion

import org.junit.Assert.*
import org.junit.Test

class CompletionHistoryTest {
    private val scope = listOf("binary", "project")
    private val anchor = CompletionPayload("a.py", "python", "pri", "")

    @Test fun `typing consumes the suggestion and backspace restores it after garbage`() {
        val history = CompletionHistory()
        history.set(scope, anchor, "nt(foo)")
        assertEquals("t(foo)", history.get(scope, anchor.copy(prefix = "prin")))
        assertNull(history.get(scope, anchor.copy(prefix = "print(foo)")))
        assertNull(history.get(scope, anchor.copy(prefix = "prixxx")))
        assertEquals("nt(foo)", history.get(scope, anchor))
        assertNull(history.get(listOf("binary", "another project"), anchor))
        assertNull(history.get(scope, anchor.copy(suffix = "changed")))
        assertNull(history.get(scope, anchor.copy(language = "javascript")))
    }

    @Test fun `full context and bounded recency prevent unrelated hits`() {
        val history = CompletionHistory(2)
        val a = anchor.copy(prefix = "import a\n" + "x".repeat(100))
        val b = a.copy(prefix = "import b\n" + "x".repeat(100))
        history.set(scope, a, "one")
        assertNull(history.get(scope, b))
        history.set(scope, b, "two")
        history.get(scope, a)
        history.set(scope, anchor, "")
        assertNull(history.get(scope, b))
        assertEquals("one", history.get(scope, a))
        assertEquals("", history.get(scope, anchor))
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
    }
}
