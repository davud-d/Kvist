package com.kvist.intellij.completion

import com.intellij.codeInsight.inline.completion.DebouncedInlineCompletionProvider
import com.intellij.codeInsight.inline.completion.InlineCompletionEvent
import com.intellij.codeInsight.inline.completion.InlineCompletionProviderID
import com.intellij.codeInsight.inline.completion.InlineCompletionRequest
import com.intellij.codeInsight.inline.completion.elements.InlineCompletionGrayTextElement
import com.intellij.codeInsight.inline.completion.suggestion.InlineCompletionSuggestion
import com.intellij.codeInsight.inline.completion.suggestion.invoke
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.util.TextRange
import com.kvist.intellij.runtime.KvistLauncher
import kotlinx.coroutines.delay
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString
import kotlin.time.Duration
import kotlin.time.Duration.Companion.milliseconds

/** Ghost text: the suggestion streams in as the model writes it and is cancelled with the editor request. */
class KvistInlineCompletionProvider : DebouncedInlineCompletionProvider() {
    override val id: InlineCompletionProviderID = InlineCompletionProviderID("Kvist")

    private val client = KvistCompletionClient()
    private val typingPauseMs = 300L

    override fun isEnabled(event: InlineCompletionEvent): Boolean {
        if (!KvistCompletionSettings.isEnabled) return false
        val documentChange = event as? InlineCompletionEvent.DocumentChange ?: return false
        return documentChange.editor.document.textLength <= MAX_DOCUMENT_CHARS
    }

    override suspend fun getDebounceDelay(request: InlineCompletionRequest): Duration = 0.milliseconds

    override suspend fun getSuggestionDebounced(request: InlineCompletionRequest): InlineCompletionSuggestion {
        val project = request.editor.project ?: return InlineCompletionSuggestion.Empty
        val target = KvistLauncher.prepareTarget(project) { message -> LOG.debug(message) }
            ?: return InlineCompletionSuggestion.Empty
        val filePath = relativeFilePath(target.workspace, request) ?: return InlineCompletionSuggestion.Empty

        val document = request.document
        val payload = CompletionPayload(
            filePath = filePath,
            language = languageFromExtension(filePath),
            prefix = document.getText(TextRange(0, request.startOffset)),
            suffix = document.getText(TextRange(request.startOffset, document.textLength)),
        )

        client.cached(target, payload)?.let { cached ->
            if (cached.isBlank()) return InlineCompletionSuggestion.Empty
            return InlineCompletionSuggestion { variant { emit(InlineCompletionGrayTextElement(cached)) } }
        }
        // Cache lookup must happen BEFORE the typing pause, so backspace/undo
        // can restore a known suggestion without a model call or another wait.
        delay(typingPauseMs)

        return InlineCompletionSuggestion {
            variant {
                try {
                    client.stream(target, payload).collect { emit(InlineCompletionGrayTextElement(it)) }
                }
                catch (exception: CompletionRequestException) {
                    LOG.debug("Kvist inline completion failed: ${exception.message}")
                }
            }
        }
    }

    private fun relativeFilePath(workspace: Path, request: InlineCompletionRequest): String? {
        val virtualFile = request.file.virtualFile ?: return null
        val filePath = try {
            Path.of(virtualFile.path).normalize()
        }
        catch (exception: java.nio.file.InvalidPathException) {
            return null
        }
        if (!filePath.startsWith(workspace)) return null
        return workspace.relativize(filePath).invariantSeparatorsPathString
    }

    // The bundled Community platform has no JS/TS/Python language plugin as a compile
    // dependency, and does not need one: the file extension alone is enough to match the
    // small set of ids the core resolver knows. Anything else is passed through as-is —
    // the core still returns a plain completion for a language it does not enrich.
    private fun languageFromExtension(filePath: String): String {
        val extension = filePath.substringAfterLast('.', missingDelimiterValue = "").lowercase()
        return EXTENSION_LANGUAGES[extension] ?: extension
    }

    companion object {
        private val LOG = logger<KvistInlineCompletionProvider>()

        // The core defensively caps its own input regardless, but there is no reason to
        // serialize a huge buffer and spawn a process client-side just to have it trimmed.
        private const val MAX_DOCUMENT_CHARS = 500_000

        private val EXTENSION_LANGUAGES = mapOf(
            "py" to "python",
            "ts" to "typescript",
            "tsx" to "typescriptreact",
            "js" to "javascript",
            "mjs" to "javascript",
            "cjs" to "javascript",
            "jsx" to "javascriptreact",
        )
    }
}
