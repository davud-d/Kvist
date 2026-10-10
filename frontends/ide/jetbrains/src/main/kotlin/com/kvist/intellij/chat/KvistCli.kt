package com.kvist.intellij.chat

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.intellij.openapi.diagnostic.logger
import com.kvist.intellij.runtime.KvistTarget
import java.io.IOException
import java.nio.charset.StandardCharsets

/** The core's one-shot modes (`--config-info`, `--config-op`, `--session-op`): one JSON reply on the last stdout line. */
internal object KvistCli {
    private val LOG = logger<KvistCli>()

    private const val READ_TIMEOUT_MS = 20_000
    private const val CONFIG_OP_TIMEOUT_MS = 30_000

    fun readJson(target: KvistTarget, args: List<String>): JsonObject? {
        return run(target, args, stdin = null, READ_TIMEOUT_MS)
    }

    // The payload may carry an API key, so it goes to stdin and is never logged.
    fun configOp(target: KvistTarget, op: String, payload: JsonObject): JsonObject {
        val timeoutMs = if (op == "set-provider" && payload.get("provider")?.asString == "chatgpt") 190_000 else CONFIG_OP_TIMEOUT_MS
        return run(target, listOf("--config-op", op), payload.toString(), timeoutMs)
            ?: failure("Kvist returned an unreadable reply.")
    }

    fun sessionOp(target: KvistTarget, op: String, name: String? = null): JsonObject? {
        val args = mutableListOf("--workspace", target.workspace.toString(), "--session-op", op)
        if (name != null) args += listOf("--session-name", name)
        return readJson(target, args)
    }

    fun failure(error: String): JsonObject {
        val reply = JsonObject()
        reply.addProperty("ok", false)
        reply.addProperty("error", error)
        return reply
    }

    private fun run(target: KvistTarget, args: List<String>, stdin: String?, timeoutMs: Int): JsonObject? {
        val commandLine = GeneralCommandLine(listOf(target.binaryPath.toString()) + args)
            .withWorkingDirectory(target.workspace)
            .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)
            .withCharset(StandardCharsets.UTF_8)
        val handler = try {
            CapturingProcessHandler(commandLine)
        }
        catch (exception: ExecutionException) {
            LOG.warn("Failed to start Kvist for ${args.firstOrNull()}", exception)
            return null
        }
        try {
            handler.processInput.use { input -> stdin?.let { input.write(it.toByteArray(StandardCharsets.UTF_8)) } }
        }
        catch (exception: IOException) {
            handler.destroyProcess()
            LOG.warn("Failed to write to Kvist for ${args.firstOrNull()}", exception)
            return null
        }
        val output = handler.runProcess(timeoutMs)
        if (output.isTimeout) {
            LOG.warn("Kvist ${args.firstOrNull()} timed out after $timeoutMs ms")
            return null
        }
        val lastLine = output.stdout.trim().lineSequence().lastOrNull().orEmpty()
        return try {
            JsonParser.parseString(lastLine) as? JsonObject
        }
        catch (_: JsonParseException) {
            LOG.warn("Unreadable Kvist reply for ${args.firstOrNull()} (exit code ${output.exitCode})")
            null
        }
    }
}
