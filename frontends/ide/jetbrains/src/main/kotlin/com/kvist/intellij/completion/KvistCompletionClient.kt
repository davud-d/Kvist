package com.kvist.intellij.completion

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.intellij.execution.process.ProcessOutput
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.progress.EmptyProgressIndicator
import com.intellij.util.concurrency.AppExecutorUtil
import com.kvist.intellij.runtime.KvistTarget
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.IOException
import java.nio.charset.StandardCharsets
import kotlin.coroutines.resume

class CompletionRequestException(message: String) : Exception(message)

/** One inline-completion request, built by the caller from the live (unsaved) [com.intellij.openapi.editor.Document]. */
data class CompletionPayload(
    val filePath: String,
    val language: String,
    val prefix: String,
    val suffix: String,
) {
    fun toJson(): String {
        val json = JsonObject()
        json.addProperty("file_path", filePath)
        json.addProperty("language", language)
        json.addProperty("prefix", prefix)
        json.addProperty("suffix", suffix)
        return json.toString()
    }
}

/** Per-request process with bounded history and cooperative cancellation. */
class KvistCompletionClient {
    private val timeoutMs = 4_000
    private val history = CompletionHistory()

    suspend fun requestCompletion(target: KvistTarget, payload: CompletionPayload): List<String> {
        val scope = listOf(target.binaryPath.toString(), target.workspace.toString())
        history.get(scope, payload)?.let { return it }
        // Cache lookup must happen BEFORE the typing pause, so backspace/undo
        // can restore known candidates without a model call or another wait.
        delay(2_000)
        val completions = withContext(Dispatchers.IO) { runCompletionProcess(target, payload) }
        history.set(scope, payload, completions)
        return completions
    }

    private suspend fun runCompletionProcess(target: KvistTarget, payload: CompletionPayload): List<String> {
        val commandLine = GeneralCommandLine(
            target.binaryPath.toString(), "--workspace", target.workspace.toString(), "--complete"
        ).withCharset(StandardCharsets.UTF_8)

        val handler = try {
            CapturingProcessHandler(commandLine)
        }
        catch (exception: ExecutionException) {
            LOG.warn("Failed to start Kvist for a completion", exception)
            throw CompletionRequestException("Failed to start Kvist.")
        }

        try {
            handler.processInput.use { it.write(payload.toJson().toByteArray(StandardCharsets.UTF_8)) }
        }
        catch (exception: IOException) {
            handler.destroyProcess()
            LOG.warn("Failed to send the completion request to Kvist", exception)
            throw CompletionRequestException("Failed to send the completion request to Kvist.")
        }

        val output = awaitProcessOutput(handler)
        if (output.isCancelled) {
            throw CancellationException("The Kvist completion request was superseded.")
        }
        if (output.isTimeout) {
            throw CompletionRequestException("Kvist did not answer within ${timeoutMs}ms.")
        }
        if (output.exitCode != 0) {
            throw CompletionRequestException("Kvist completion process failed.")
        }
        return parseCompletionReply(output.stdout)
    }

    /**
     * Bridges the blocking, [EmptyProgressIndicator]-driven process wait onto a suspend
     * function: `invokeOnCancellation` fires the instant this coroutine is cancelled — not
     * only once the blocking call eventually returns — because a plain blocking call inside
     * `withContext` has no suspension point of its own for cancellation to interrupt.
     */
    private suspend fun awaitProcessOutput(handler: CapturingProcessHandler): ProcessOutput {
        val indicator = EmptyProgressIndicator()
        return suspendCancellableCoroutine { continuation ->
            continuation.invokeOnCancellation { indicator.cancel() }
            AppExecutorUtil.getAppExecutorService().execute {
                val result = handler.runProcessWithProgressIndicator(indicator, timeoutMs)
                if (continuation.isActive) {
                    continuation.resume(result)
                }
            }
        }
    }

    companion object {
        private val LOG = logger<KvistCompletionClient>()
    }
}

internal fun parseCompletionReply(stdout: String): List<String> {
    val reply = try {
        JsonParser.parseString(stdout.trim().lineSequence().last()) as? JsonObject
    } catch (_: JsonParseException) {
        null
    } ?: throw CompletionRequestException("Kvist returned an unreadable reply.")
    val ok = reply.get("ok")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isBoolean }?.asBoolean == true
    if (!ok) throw CompletionRequestException("Kvist could not generate a completion.")
    val candidates = reply.get("completions")?.takeIf { it.isJsonArray }?.asJsonArray
        ?.mapNotNull { it.takeIf { value -> value.isJsonPrimitive && value.asJsonPrimitive.isString }?.asString }
        ?: listOfNotNull(reply.get("completion")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString)
    return candidates.filter { it.isNotBlank() }.map { it.take(2000) }.distinct().take(3)
}
