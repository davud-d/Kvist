package com.kvist.intellij.chat

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.ide.util.PropertiesComponent

/**
 * The environment the core starts with: the VS Code `kvist.environment` setting, kept in
 * the IDE's application properties. Values are plain text, like in VS Code.
 */
internal object KvistEnvironment {
    private const val KEY = "kvist.environment"
    private val NAME = Regex("^[A-Za-z_][A-Za-z0-9_]*$")

    data class Split(val valid: Map<String, String>, val rejected: List<String>)

    fun load(): Split {
        val saved = PropertiesComponent.getInstance().getValue(KEY) ?: return Split(emptyMap(), emptyList())
        val parsed = try {
            JsonParser.parseString(saved)
        }
        catch (_: JsonParseException) {
            null
        }
        return split(parsed)
    }

    fun save(valid: Map<String, String>) {
        PropertiesComponent.getInstance().setValue(KEY, toJson(valid).toString())
    }

    // Nothing about the shape is assumed: a name that is not a plain identifier, or a value
    // that is not text, never reaches the child process.
    fun split(value: JsonElement?): Split {
        if (value !is JsonObject) return Split(emptyMap(), emptyList())
        val valid = linkedMapOf<String, String>()
        val rejected = mutableListOf<String>()
        for ((name, item) in value.entrySet()) {
            val text = if (item.isJsonPrimitive && item.asJsonPrimitive.isString) item.asString else null
            if (NAME.matches(name) && text != null && !text.contains('\u0000')) {
                valid[name] = text
            }
            else {
                rejected += name
            }
        }
        return Split(valid, rejected)
    }

    fun toJson(values: Map<String, String>): JsonObject {
        val result = JsonObject()
        values.forEach { (name, value) -> result.addProperty(name, value) }
        return result
    }
}
