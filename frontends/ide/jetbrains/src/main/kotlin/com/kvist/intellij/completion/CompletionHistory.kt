package com.kvist.intellij.completion

/**
 * What to put in the editor: [text] replaces the span from [before] UTF-16 units left of the caret to [after] units
 * right of it. Either is negative when the span lies wholly on one side of the caret, elsewhere on its line.
 */
data class Completion(val text: String, val before: Int = 0, val after: Int = 0) {
    val isReplacement: Boolean get() = before != 0 || after != 0
}

/** Bounded history of exact editor states; full context prevents cross-project/line collisions.
 * Typing consumes a continuation; a replacement is reused only at its exact anchor, since typing changes what it replaces. */
internal class CompletionHistory(private val capacity: Int = 50) {
    private data class Key(val scope: List<String>, val payload: CompletionPayload)
    private val entries = object : LinkedHashMap<Key, Completion>(capacity, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Key, Completion>): Boolean = size > capacity
    }

    @Synchronized
    fun get(scope: List<String>, payload: CompletionPayload): Completion? {
        entries[Key(scope, payload)]?.let { return it }
        for ((key, value) in entries.entries.toList().asReversed()) {
            if (value.isReplacement) continue
            val old = key.payload
            if (scope != key.scope || payload.filePath != old.filePath || payload.language != old.language ||
                payload.suffix != old.suffix || !payload.prefix.startsWith(old.prefix)) continue
            val typed = payload.prefix.substring(old.prefix.length)
            if (!value.text.startsWith(typed)) continue
            val rest = value.text.substring(typed.length)
            if (rest.isNotEmpty()) return Completion(rest)
        }
        return null
    }

    @Synchronized
    fun set(scope: List<String>, payload: CompletionPayload, completion: Completion) {
        entries[Key(scope.toList(), payload)] = completion
    }
}
