package com.kvist.intellij.completion

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.openapi.diagnostic.logger
import com.kvist.intellij.runtime.KvistTarget
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.withTimeoutOrNull
import java.io.BufferedWriter
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread

class CompletionRequestException(message: String) : Exception(message)

/** One inline-completion request, built by the caller from the live (unsaved) [com.intellij.openapi.editor.Document]. */
data class CompletionPayload(
    val filePath: String,
    val language: String,
    val prefix: String,
    val suffix: String,
) {
    fun toRequest(id: Long): JsonObject {
        val json = JsonObject()
        json.addProperty("id", id)
        json.addProperty("file_path", filePath)
        json.addProperty("language", language)
        json.addProperty("prefix", prefix)
        json.addProperty("suffix", suffix)
        // This editor can draw a deletion, so the core may offer a replacement.
        json.addProperty("edits", true)
        return json
    }
}

/**
 * One long-lived `kvist --complete-server` per binary and workspace (protocol in
 * `kernel/ide/completion_server.py`). A process per keystroke paid interpreter start-up
 * and a new TLS connection every time. The reply arrives in pieces that
 * [stream] passes on, so the editor shows text while the model is still writing.
 */
class KvistCompletionClient {
    private val timeoutMs = 6_000L
    private val maxCompletionChars = 2_000
    private val history = CompletionHistory()
    private var server: CompletionServer? = null

    fun cached(target: KvistTarget, payload: CompletionPayload): Completion? {
        if (CompletionFilePolicy.isBlocked(target.workspace, payload.filePath)) return null
        return history.get(scopeOf(target), payload)
    }

    /** Continuations arrive as pieces; a replacement arrives whole, as the only item. */
    fun stream(target: KvistTarget, payload: CompletionPayload): Flow<Completion> = channelFlow {
        if (CompletionFilePolicy.isBlocked(target.workspace, payload.filePath)) return@channelFlow
        val server = serverFor(target)
        val events = Channel<JsonObject>(Channel.UNLIMITED)
        val id = server.start(payload) { events.trySend(it) }
        val text = StringBuilder()
        var replacement: Completion? = null
        var finished = false
        try {
            withTimeoutOrNull(timeoutMs) {
                for (event in events) {
                    val replace = event.get("replace")?.takeIf { it.isJsonObject }?.asJsonObject
                    if (replace != null) {
                        replacement = parseReplacement(replace) ?: break
                        send(replacement)
                        continue
                    }
                    val delta = event.get("delta")?.takeIf { it.isJsonPrimitive }?.asString
                    if (delta == null) {
                        finished = event.get("done")?.takeIf { it.isJsonPrimitive }?.asBoolean == true
                        break
                    }
                    text.append(delta)
                    send(Completion(delta))
                }
            }
        }
        finally {
            server.finish(id, cancel = !finished)
        }
        if (finished) history.set(scopeOf(target), payload, replacement ?: Completion(text.take(maxCompletionChars).toString()))
    }

    @Synchronized
    private fun serverFor(target: KvistTarget): CompletionServer {
        val key = scopeOf(target)
        server?.takeIf { it.key == key && it.isAlive }?.let { return it }
        server?.close()
        return CompletionServer.start(key, target).also { server = it }
    }

    private fun scopeOf(target: KvistTarget) = listOf(target.binaryPath.toString(), target.workspace.toString())

    companion object {
        private val LOG = logger<KvistCompletionClient>()
    }
}

/** The child process: requests go to its stdin as JSON lines, replies come back the same way. */
private class CompletionServer private constructor(val key: List<String>, private val process: Process) {
    private val listeners = ConcurrentHashMap<Long, (JsonObject) -> Unit>()
    private val nextId = AtomicLong(1)
    private val writer: BufferedWriter = process.outputStream.bufferedWriter(StandardCharsets.UTF_8)

    val isAlive: Boolean get() = process.isAlive

    init {
        thread(isDaemon = true, name = "Kvist completion replies") { readReplies() }
        thread(isDaemon = true, name = "Kvist completion log") {
            process.errorStream.bufferedReader(StandardCharsets.UTF_8).forEachLine { LOG.debug(it) }
        }
    }

    fun start(payload: CompletionPayload, listener: (JsonObject) -> Unit): Long {
        val id = nextId.getAndIncrement()
        listeners[id] = listener
        try {
            send(payload.toRequest(id))
        }
        catch (exception: IOException) {
            listeners.remove(id)
            close()
            throw CompletionRequestException("Failed to send the completion request to Kvist.")
        }
        return id
    }

    /** Forgets the request; when it did not finish, also tells the process to stop generating. */
    fun finish(id: Long, cancel: Boolean) {
        if (listeners.remove(id) == null || !cancel) return
        val message = JsonObject()
        message.addProperty("cancel", id)
        try {
            send(message)
        }
        catch (exception: IOException) {
            LOG.debug("Kvist completion process is gone: ${exception.message}")
        }
    }

    fun close() {
        process.destroy()
    }

    @Synchronized
    private fun send(message: JsonObject) {
        writer.write(message.toString())
        writer.newLine()
        writer.flush()
    }

    private fun readReplies() {
        process.inputStream.bufferedReader(StandardCharsets.UTF_8).forEachLine { line ->
            val reply = parseServerLine(line) ?: return@forEachLine
            listeners[reply.get("id").asLong]?.invoke(reply)
        }
        val orphaned = listeners.values.toList()
        listeners.clear()
        val failure = JsonObject()
        failure.addProperty("error", "The Kvist completion process exited.")
        orphaned.forEach { it(failure) }
    }

    companion object {
        private val LOG = logger<CompletionServer>()

        fun start(key: List<String>, target: KvistTarget): CompletionServer {
            val commandLine = GeneralCommandLine(
                target.binaryPath.toString(), "--workspace", target.workspace.toString(), "--complete-server"
            ).withCharset(StandardCharsets.UTF_8)
            val process = try {
                commandLine.createProcess()
            }
            catch (exception: ExecutionException) {
                LOG.warn("Failed to start Kvist for completions", exception)
                throw CompletionRequestException("Failed to start Kvist.")
            }
            return CompletionServer(key, process)
        }
    }
}

/** The `replace` object of a reply; null when it is not a text with whole offsets, so a bad one never becomes a range. */
internal fun parseReplacement(replace: JsonObject): Completion? {
    val text = replace.get("text")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString ?: return null
    val before = replace.wholeOffset("before") ?: return null
    val after = replace.wholeOffset("after") ?: return null
    return Completion(text, before, after).takeIf { it.isReplacement }
}

private fun JsonObject.wholeOffset(name: String): Int? {
    val value = get(name)?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asBigDecimal ?: return null
    return runCatching { value.intValueExact() }.getOrNull()
}

/** One stdout line as a reply object with a numeric `id`; anything else (a stray warning) is not a reply. */
internal fun parseServerLine(line: String): JsonObject? {
    val reply = try {
        JsonParser.parseString(line) as? JsonObject
    } catch (_: JsonParseException) {
        null
    } ?: return null
    val id = reply.get("id")
    return reply.takeIf { id != null && id.isJsonPrimitive && id.asJsonPrimitive.isNumber }
}
