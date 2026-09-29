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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Collections
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

/**
 * Runs `kvist --workspace <dir> --complete` for one inline-completion request and caches
 * the answer. Modeled on `KvistCommitMessageClient`'s process-handling shape, with two
 * differences a per-keystroke request needs that a per-click one does not: a short
 * timeout, and cancellation driven by the calling coroutine rather than a
 * caller-owned [com.intellij.openapi.progress.ProgressIndicator] — the inline-completion
 * platform API hands every request its own coroutine, cancelled the moment it is
 * superseded by further typing, and that cancellation is what must kill the child process.
 */
class KvistCompletionClient {
    // Keystroke-latency-critical: unlike a commit message (one click, one wait), a stale
    // answer here is worse than none, and letting requests pile up behind a slow one would
    // make typing feel laggy. Chosen well above a fast model's typical reply time, well
    // below where a suggestion would arrive too late to still match the caret position.
    private val timeoutMs = 4_000

    // Bounded so a long editing session cannot grow this without limit; large enough that
    // retyping over an undo, or revisiting a line, is likely to still hit.
    private val cache: MutableMap<String, String> = Collections.synchronizedMap(
        object : LinkedHashMap<String, String>(CACHE_CAPACITY, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>): Boolean {
                return size > CACHE_CAPACITY
            }
        }
    )

    suspend fun requestCompletion(target: KvistTarget, payload: CompletionPayload): String {
        cache[cacheKey(payload)]?.let { return it }

        val completion = runCompletionProcess(target, payload)
        cache[cacheKey(payload)] = completion
        return completion
    }

    private suspend fun runCompletionProcess(target: KvistTarget, payload: CompletionPayload): String {
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
        return parseReply(output.stdout)
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

    private fun parseReply(stdout: String): String {
        val reply = try {
            JsonParser.parseString(stdout.trim()) as? JsonObject
        }
        catch (exception: JsonParseException) {
            LOG.warn("Unreadable Kvist completion reply: $stdout", exception)
            null
        } ?: throw CompletionRequestException("Kvist returned an unreadable reply.")

        val ok = reply.get("ok")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: false
        if (!ok) {
            val error = reply.get("error")?.takeIf { it.isJsonPrimitive }?.asString
            throw CompletionRequestException(error ?: "Kvist could not generate a completion.")
        }
        // An empty string is a valid "no suggestion" answer, not a missing field.
        return reply.get("completion")?.takeIf { it.isJsonPrimitive }?.asString ?: ""
    }

    companion object {
        private val LOG = logger<KvistCompletionClient>()
        private const val CACHE_CAPACITY = 200
        private const val PREFIX_TAIL_CHARS = 80
        private const val SUFFIX_HEAD_CHARS = 50

        private fun cacheKey(payload: CompletionPayload): String {
            val prefixTail = payload.prefix.takeLast(PREFIX_TAIL_CHARS)
            val suffixHead = payload.suffix.take(SUFFIX_HEAD_CHARS)
            val digest = MessageDigest.getInstance("SHA-256")
                .digest("$prefixTail\u0000$suffixHead".toByteArray(StandardCharsets.UTF_8))
            return payload.filePath + ":" + digest.joinToString("") { "%02x".format(it) }
        }
    }
}
