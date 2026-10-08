package com.kvist.intellij.completion

import org.junit.Assert.*
import org.junit.Test

class CompletionHistoryTest {
    private val scope = listOf("binary", "project")
    private val anchor = CompletionPayload("a.py", "python", "pri", "")

    @Test fun `typing consumes alternatives and backspace restores them after garbage`() {
        val history = CompletionHistory()
        history.set(scope, anchor, listOf("nt(foo)", "nt(bar)"))
        assertEquals(listOf("t(foo)", "t(bar)"), history.get(scope, anchor.copy(prefix = "prin")))
        assertNull(history.get(scope, anchor.copy(prefix = "prixxx")))
        assertEquals(listOf("nt(foo)", "nt(bar)"), history.get(scope, anchor))
        assertNull(history.get(listOf("binary", "another project"), anchor))
        assertNull(history.get(scope, anchor.copy(suffix = "changed")))
        assertNull(history.get(scope, anchor.copy(language = "javascript")))
    }

    @Test fun `full context and bounded recency prevent unrelated hits`() {
        val history = CompletionHistory(2)
        val a = anchor.copy(prefix = "import a\n" + "x".repeat(100))
        val b = a.copy(prefix = "import b\n" + "x".repeat(100))
        history.set(scope, a, listOf("one"))
        assertNull(history.get(scope, b))
        history.set(scope, b, listOf("two"))
        history.get(scope, a)
        history.set(scope, anchor, emptyList())
        assertNull(history.get(scope, b))
        assertEquals(listOf("one"), history.get(scope, a))
        assertEquals(emptyList<String>(), history.get(scope, anchor))
    }

    @Test fun `wire supports alternatives and old cores`() {
        assertEquals(listOf("a", "b"), parseCompletionReply("""{"ok":true,"completions":["a","b","a",4,""]}"""))
        assertEquals(listOf("old"), parseCompletionReply("""{"ok":true,"completion":"old"}"""))
        assertEquals(emptyList<String>(), parseCompletionReply("""{"ok":true,"completions":[]}"""))
    }

    @Test(expected = CompletionRequestException::class)
    fun `invalid reply fails without caching a suggestion`() {
        parseCompletionReply("not json")
    }
}
