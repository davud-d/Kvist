package com.kvist.intellij.selection.prompt

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser

/** Turns raw model text into something the apply step can trust the shape of. */
object ModelReply {
    private val FENCED = Regex("""^```[\w+#.-]*[ \t]*\n(.*?)\n?```\s*$""", RegexOption.DOT_MATCHES_ALL)

    /**
     * The replacement text for [original], or `null` when the model answered with nothing.
     * The selection's own leading and trailing newlines are kept, so the replacement fits the
     * same lines the selection covered whatever blank lines the model added or dropped.
     */
    fun replacementFor(original: String, reply: String): String? {
        val unfenced = stripFence(reply.replace("\r\n", "\n").replace('\r', '\n'))
        val body = unfenced.trim('\n')
        if (body.isBlank()) return null

        val leading = original.takeWhile { it == '\n' }
        val trailing = original.takeLastWhile { it == '\n' }
        return leading + body + trailing
    }

    fun stripFence(text: String): String {
        val trimmed = text.trim()
        return FENCED.find(trimmed)?.groupValues?.get(1) ?: text
    }
}

data class ExplainAndFixReport(
    val explanation: String,
    val risks: List<String>,
    val bugs: List<String>,
    val patch: String?,
    val patchSafety: String?,
) {
    companion object {
        /**
         * Reads the JSON object the prompt asks for. A reply that is not that object is still
         * worth showing, so it becomes the explanation, with no patch.
         */
        fun parse(reply: String): ExplainAndFixReport? {
            val text = ModelReply.stripFence(reply.trim()).trim()
            if (text.isEmpty()) return null

            val json = parseObject(text)
                ?: return ExplainAndFixReport(text, emptyList(), emptyList(), null, null)

            val patch = json.string("patch")?.let { ModelReply.stripFence(it.replace("\r\n", "\n")) }
            val explanation = json.string("explanation").orEmpty()
            val report = ExplainAndFixReport(
                explanation = explanation,
                risks = json.strings("risks"),
                bugs = json.strings("bugs"),
                patch = patch?.takeIf { it.isNotBlank() },
                patchSafety = json.string("patch_safety")?.takeIf { it.isNotBlank() },
            )
            val empty = report.explanation.isBlank() && report.risks.isEmpty() &&
                report.bugs.isEmpty() && report.patch == null
            return if (empty) null else report
        }

        private fun parseObject(text: String): JsonObject? {
            val start = text.indexOf('{')
            val end = text.lastIndexOf('}')
            if (start < 0 || end <= start) return null
            return try {
                JsonParser.parseString(text.substring(start, end + 1)) as? JsonObject
            }
            catch (_: JsonParseException) {
                null
            }
        }

        private fun JsonObject.string(name: String): String? {
            val element: JsonElement = get(name) ?: return null
            return if (element.isJsonPrimitive) element.asString else null
        }

        private fun JsonObject.strings(name: String): List<String> {
            val array = get(name) as? JsonArray ?: return emptyList()
            return array.filter { it.isJsonPrimitive }.map { it.asString }.filter { it.isNotBlank() }
        }
    }
}
