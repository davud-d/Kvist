package com.kvist.intellij.chat

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.openapi.diagnostic.logger
import java.io.IOException
import java.io.InputStream
import java.io.OutputStreamWriter
import java.io.Writer
import java.nio.charset.StandardCharsets
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** UI_API.md §4: the first event must be `hello` with this version. */
private const val PROTOCOL_VERSION = 1

interface KvistCoreListener {
    fun onEvent(event: JsonObject)
    fun onDiagnostic(text: String)
    fun onFault(message: String)
    fun onExit(code: Int?, expected: Boolean)
}

/**
 * The core as a child process in mode B (`--frontend stdio-multi`): NDJSON events on
 * stdout, goals and replies on stdin. A Kotlin port of the shared `src/core.ts`.
 */
class KvistCoreProcess(
    private val binary: Path,
    private val workspace: Path,
    private val environment: Map<String, String>,
    private val listener: KvistCoreListener,
) {
    private val lock = Any()
    private val pending = ConcurrentHashMap<String, String>()
    private var process: Process? = null
    private var stdin: Writer? = null
    @Volatile private var stopping = false
    @Volatile private var refused = false

    val running: Boolean
        get() = synchronized(lock) { process != null }

    @Throws(ExecutionException::class)
    fun start() {
        val commandLine = GeneralCommandLine(
            binary.toString(),
            "--workspace", workspace.toString(),
            "--frontend", "stdio-multi",
            "--role", "auto",
            "--approval-mode", "ask",
        )
            .withWorkingDirectory(workspace)
            .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)
            .withEnvironment(environment)
            .withCharset(StandardCharsets.UTF_8)
        val started = commandLine.createProcess()
        synchronized(lock) {
            process = started
            stdin = OutputStreamWriter(started.outputStream, StandardCharsets.UTF_8)
        }
        thread(name = "Kvist core stderr", isDaemon = true) { readDiagnostics(started.errorStream) }
        thread(name = "Kvist core stdout", isDaemon = true) { readEvents(started) }
    }

    // A goal is one stdin line, so line breaks are flattened.
    fun send(goal: String) {
        val line = goal.replace(Regex("\r?\n"), " ").trim()
        if (line.isNotEmpty()) {
            writeLine(line)
        }
    }

    // A slash command that runs inside the core (UI_API.md §8c).
    fun sendCommand(name: String, args: List<String>) {
        val payload = JsonObject()
        payload.addProperty("type", "command")
        payload.addProperty("name", name)
        payload.add("args", jsonArray(args))
        writeLine(payload.toString())
    }

    // Approvals are fail-closed: only a literal `true` approves, and `remember` travels only with it.
    fun respond(requestId: String, answer: Any?, remember: Boolean) {
        val kind = pending.remove(requestId) ?: return
        val reply = JsonObject()
        reply.addProperty("request_id", requestId)
        if (kind == "confirm") {
            val approved = answer == true
            reply.addProperty("approved", approved)
            if (approved && remember) {
                reply.addProperty("remember", true)
            }
        }
        else {
            reply.addProperty("value", answer as? String)
        }
        writeLine(reply.toString())
    }

    // "Stop all": the turn, its helpers, calls and every process the task owns.
    fun cancel() {
        writeLine("""{"type":"cancel"}""")
    }

    // Pause at the next safe point; the task and its live resources stay.
    fun pause() {
        writeLine("""{"type":"pause"}""")
    }

    fun stopHelper(agentId: String) {
        val message = JsonObject()
        message.addProperty("type", "helper_stop")
        message.addProperty("agent_id", agentId)
        writeLine(message.toString())
    }

    // Closing stdin is the core's normal exit signal; open requests are denied first.
    fun stop(): CompletableFuture<Void?> {
        val child = synchronized(lock) { process } ?: return CompletableFuture.completedFuture(null)
        stopping = true
        denyPending()
        closeStdin()
        val exited = child.onExit().thenApply<Void?> { null }
        CompletableFuture.delayedExecutor(500, TimeUnit.MILLISECONDS).execute { child.destroy() }
        CompletableFuture.delayedExecutor(2500, TimeUnit.MILLISECONDS).execute { child.destroyForcibly() }
        return exited
    }

    private fun denyPending() {
        for ((id, kind) in pending) {
            val reply = JsonObject()
            reply.addProperty("request_id", id)
            if (kind == "confirm") reply.addProperty("approved", false) else reply.add("value", null)
            tryWriteLine(reply.toString())
        }
        pending.clear()
    }

    private fun readEvents(child: Process) {
        var greeted = false
        try {
            child.inputStream.bufferedReader(StandardCharsets.UTF_8).use { reader ->
                reader.lineSequence().forEach { line -> greeted = decode(line, greeted) }
            }
        }
        catch (exception: IOException) {
            LOG.info("Kvist core stdout closed: ${exception.message}")
        }
        val code = try {
            child.waitFor()
        }
        catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            null
        }
        synchronized(lock) {
            process = null
            stdin = null
        }
        pending.clear()
        listener.onExit(code, stopping)
    }

    private fun decode(raw: String, greeted: Boolean): Boolean {
        val line = raw.trim()
        if (line.isEmpty() || refused) return greeted
        val event = try {
            JsonParser.parseString(line) as? JsonObject
        }
        catch (_: JsonParseException) {
            null
        }
        if (event == null) {
            listener.onFault("Core emitted invalid JSON: ${line.take(120)}")
            return greeted
        }
        val type = event.stringField("type")
        if (!greeted && (type != "hello" || event.intField("protocol_version") != PROTOCOL_VERSION)) {
            refused = true
            listener.onFault("Unsupported core handshake (expected hello, protocol $PROTOCOL_VERSION).")
            stop()
            return false
        }
        if (type == "input_request") {
            val id = event.stringField("request_id")
            val kind = event.stringField("kind")
            if (id != null && kind != null) pending[id] = kind
        }
        listener.onEvent(event)
        return true
    }

    // Forwarded as it arrives, in chunks, like the VS Code host does.
    private fun readDiagnostics(stream: InputStream) {
        val buffer = CharArray(8192)
        try {
            stream.reader(StandardCharsets.UTF_8).use { reader ->
                while (true) {
                    val count = reader.read(buffer)
                    if (count < 0) break
                    listener.onDiagnostic(String(buffer, 0, count))
                }
            }
        }
        catch (exception: IOException) {
            LOG.info("Kvist core stderr closed: ${exception.message}")
        }
    }

    private fun writeLine(line: String) {
        synchronized(lock) {
            val writer = stdin ?: throw IOException("Kvist core is not accepting input.")
            writer.write(line)
            writer.write("\n")
            writer.flush()
        }
    }

    private fun tryWriteLine(line: String) {
        try {
            writeLine(line)
        }
        catch (exception: IOException) {
            LOG.info("Could not deny an open Kvist request: ${exception.message}")
        }
    }

    private fun closeStdin() {
        synchronized(lock) {
            try {
                stdin?.close()
            }
            catch (exception: IOException) {
                LOG.info("Kvist core stdin was already closed: ${exception.message}")
            }
            stdin = null
        }
    }

    private companion object {
        val LOG = logger<KvistCoreProcess>()
    }
}
