package com.kvist.intellij.chat

import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject

internal fun JsonObject.stringField(name: String): String? {
    val value = get(name)
    return if (value != null && value.isJsonPrimitive && value.asJsonPrimitive.isString) value.asString else null
}

internal fun JsonObject.intField(name: String): Int? {
    val value = get(name)
    return if (value != null && value.isJsonPrimitive && value.asJsonPrimitive.isNumber) value.asInt else null
}

internal fun JsonObject.booleanField(name: String): Boolean? {
    val value = get(name)
    return if (value != null && value.isJsonPrimitive && value.asJsonPrimitive.isBoolean) value.asBoolean else null
}

internal fun JsonObject.stringList(name: String): List<String> {
    val value = get(name)
    if (value == null || !value.isJsonArray) return emptyList()
    return value.asJsonArray.mapNotNull { item -> if (item.isJsonPrimitive && item.asJsonPrimitive.isString) item.asString else null }
}

internal fun JsonObject.objectList(name: String): List<JsonObject> {
    val value = get(name)
    if (value == null || !value.isJsonArray) return emptyList()
    return value.asJsonArray.mapNotNull { it as? JsonObject }
}

internal fun jsonArray(items: Iterable<String>): JsonArray {
    val array = JsonArray()
    items.forEach { array.add(it) }
    return array
}

internal fun jsonArrayOf(items: Iterable<JsonElement>): JsonArray {
    val array = JsonArray()
    items.forEach { array.add(it) }
    return array
}

internal fun message(type: String, fill: JsonObject.() -> Unit = {}): JsonObject {
    val result = JsonObject()
    result.addProperty("type", type)
    result.fill()
    return result
}
