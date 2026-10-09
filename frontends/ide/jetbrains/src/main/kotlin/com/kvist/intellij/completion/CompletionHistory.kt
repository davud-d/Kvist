package com.kvist.intellij.completion

/** Bounded history of exact editor states; full context prevents cross-project/line collisions. */
internal class CompletionHistory(private val capacity: Int = 50) {
    private data class Key(val scope: List<String>, val payload: CompletionPayload)
    private val entries = object : LinkedHashMap<Key, String>(capacity, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Key, String>): Boolean = size > capacity
    }

    @Synchronized
    fun get(scope: List<String>, payload: CompletionPayload): String? {
        entries[Key(scope, payload)]?.let { return it }
        for ((key, value) in entries.entries.toList().asReversed()) {
            val old = key.payload
            if (scope != key.scope || payload.filePath != old.filePath || payload.language != old.language ||
                payload.suffix != old.suffix || !payload.prefix.startsWith(old.prefix)) continue
            val typed = payload.prefix.substring(old.prefix.length)
            if (!value.startsWith(typed)) continue
            val rest = value.substring(typed.length)
            if (rest.isNotEmpty()) return rest
        }
        return null
    }

    @Synchronized
    fun set(scope: List<String>, payload: CompletionPayload, completion: String) {
        entries[Key(scope.toList(), payload)] = completion
    }
}
