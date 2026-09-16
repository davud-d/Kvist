package com.kvist.intellij.commit

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.progress.ProcessCanceledException
import com.intellij.openapi.progress.ProgressIndicator
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Path

class CommitMessageException(message: String) : Exception(message)

/** Runs `kvist --commit-message` and returns the generated message. */
object KvistCommitMessageClient {
    private val LOG = logger<KvistCommitMessageClient>()

    // A reasoning model on a large diff can take a while; beyond this the
    // user is better served by an error than by a spinner.
    private const val TIMEOUT_MS = 120_000

    fun generate(binaryPath: Path, payload: String, indicator: ProgressIndicator): String {
        val commandLine = GeneralCommandLine(binaryPath.toString(), "--commit-message")
            .withCharset(StandardCharsets.UTF_8)

        val handler = try {
            CapturingProcessHandler(commandLine)
        }
        catch (exception: ExecutionException) {
            LOG.warn("Failed to start Kvist for a commit message", exception)
            throw CommitMessageException("Failed to start Kvist.")
        }

        try {
            handler.processInput.use { it.write(payload.toByteArray(StandardCharsets.UTF_8)) }
        }
        catch (exception: IOException) {
            handler.destroyProcess()
            LOG.warn("Failed to send changes to Kvist", exception)
            throw CommitMessageException("Failed to send changes to Kvist.")
        }

        val output = handler.runProcessWithProgressIndicator(indicator, TIMEOUT_MS)
        if (output.isCancelled) {
            throw ProcessCanceledException()
        }
        if (output.isTimeout) {
            throw CommitMessageException("Kvist did not answer within ${TIMEOUT_MS / 1000} seconds.")
        }
        if (output.stderr.isNotBlank()) {
            LOG.info("Kvist commit-message stderr: ${output.stderr.trim()}")
        }
        return parseReply(output.stdout)
    }

    private fun parseReply(stdout: String): String {
        val reply = try {
            JsonParser.parseString(stdout.trim()) as? JsonObject
        }
        catch (exception: JsonParseException) {
            LOG.warn("Unreadable Kvist reply: $stdout", exception)
            null
        } ?: throw CommitMessageException("Kvist returned an unreadable reply.")

        val ok = reply.get("ok")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: false
        if (!ok) {
            val error = reply.get("error")?.takeIf { it.isJsonPrimitive }?.asString
            throw CommitMessageException(error ?: "Kvist could not generate a commit message.")
        }
        return reply.get("message")?.takeIf { it.isJsonPrimitive }?.asString
            ?: throw CommitMessageException("Kvist returned no commit message.")
    }
}
