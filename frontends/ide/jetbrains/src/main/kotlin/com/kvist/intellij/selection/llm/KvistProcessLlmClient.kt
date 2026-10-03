package com.kvist.intellij.selection.llm

import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.execution.process.CapturingProcessHandler
import com.intellij.execution.process.ProcessOutput
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.progress.EmptyProgressIndicator
import com.intellij.openapi.project.Project
import com.intellij.util.concurrency.AppExecutorUtil
import com.kvist.intellij.runtime.KvistLauncher
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.IOException
import java.nio.charset.StandardCharsets
import kotlin.coroutines.resume

/**
 * Sends one request to `kvist --selection-task`: the same short-lived headless process
 * shape as inline completion and commit messages, so the provider, model and key are
 * the ones saved in `~/.kvist/config.json`.
 */
class KvistProcessLlmClient(private val project: Project) : KvistLlmClient {

    override suspend fun complete(request: KvistLlmRequest): KvistLlmResponse {
        val handler = withContext(Dispatchers.IO) { startProcess(request) }
        val output = awaitProcessOutput(handler)
        if (output.isCancelled) {
            throw CancellationException("The Kvist request was cancelled.")
        }
        if (output.isTimeout) {
            throw KvistLlmException("Kvist did not answer within ${TIMEOUT_MS / 1000} seconds.")
        }
        if (output.stderr.isNotBlank()) {
            LOG.info("Kvist selection-task stderr: ${output.stderr.trim()}")
        }
        return KvistLlmResponse(parseReply(output))
    }

    private fun startProcess(request: KvistLlmRequest): CapturingProcessHandler {
        var failure: String? = null
        val target = KvistLauncher.prepareTarget(project) { message -> failure = message }
            ?: throw KvistLlmException(failure ?: "Failed to start Kvist.")

        val commandLine = GeneralCommandLine(target.binaryPath.toString(), "--selection-task")
            .withCharset(StandardCharsets.UTF_8)
        val handler = try {
            CapturingProcessHandler(commandLine)
        }
        catch (exception: ExecutionException) {
            LOG.warn("Failed to start Kvist for a selection task", exception)
            throw KvistLlmException("Failed to start Kvist.")
        }

        val payload = JsonObject()
        payload.addProperty("system", request.systemPrompt)
        payload.addProperty("user", request.userPrompt)
        try {
            handler.processInput.use { it.write(payload.toString().toByteArray(StandardCharsets.UTF_8)) }
        }
        catch (exception: IOException) {
            handler.destroyProcess()
            LOG.warn("Failed to send the selection task to Kvist", exception)
            throw KvistLlmException("Failed to send the request to Kvist.")
        }
        return handler
    }

    // The blocking wait has no suspension point of its own, so cancellation is bridged
    // explicitly: cancelling the coroutine cancels the indicator, which kills the process.
    private suspend fun awaitProcessOutput(handler: CapturingProcessHandler): ProcessOutput {
        val indicator = EmptyProgressIndicator()
        return suspendCancellableCoroutine { continuation ->
            continuation.invokeOnCancellation { indicator.cancel() }
            AppExecutorUtil.getAppExecutorService().execute {
                val result = handler.runProcessWithProgressIndicator(indicator, TIMEOUT_MS)
                if (continuation.isActive) {
                    continuation.resume(result)
                }
            }
        }
    }

    private fun parseReply(output: ProcessOutput): String {
        val reply = try {
            JsonParser.parseString(output.stdout.trim()) as? JsonObject
        }
        catch (exception: JsonParseException) {
            LOG.warn("Unreadable Kvist reply: ${output.stdout}", exception)
            null
        } ?: throw KvistLlmException(unreadableReplyMessage(output.stderr))

        val ok = reply.get("ok")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: false
        if (!ok) {
            val error = reply.get("error")?.takeIf { it.isJsonPrimitive }?.asString
            throw KvistLlmException(error ?: "Kvist could not complete the request.")
        }
        return reply.get("text")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty()
    }

    // An older bundled binary rejects the flag in argparse and prints nothing on stdout.
    private fun unreadableReplyMessage(stderr: String): String {
        return if ("--selection-task" in stderr) {
            "The bundled Kvist binary is too old for editor actions. Rebuild it."
        }
        else {
            "Kvist returned an unreadable reply."
        }
    }

    companion object {
        private val LOG = logger<KvistProcessLlmClient>()

        // A rewrite or an explanation of a large selection on a reasoning model can be slow;
        // beyond this an error is better than a spinner.
        private const val TIMEOUT_MS = 120_000
    }
}
