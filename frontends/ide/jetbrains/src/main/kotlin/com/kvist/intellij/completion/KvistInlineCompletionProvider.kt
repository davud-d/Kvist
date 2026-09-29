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
import kotlinx.coroutines.CancellationException
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString
import kotlin.time.Duration
import kotlin.time.Duration.Companion.milliseconds

/**
 * Ghost-text inline completion backed by `kvist --complete`. Extends
 * [DebouncedInlineCompletionProvider] rather than [com.intellij.codeInsight.inline.completion.InlineCompletionProvider]
 * directly: the platform base class already gives every request its own coroutine,
 * cancels the previous one the instant a new one supersedes it, and waits
 * [getDebounceDelay] before calling [getSuggestionDebounced] at all — exactly the
 * debounce-and-cancel behavior this feature needs, so there is nothing to hand-roll here.
 */
class KvistInlineCompletionProvider : DebouncedInlineCompletionProvider() {
    override val id: InlineCompletionProviderID = InlineCompletionProviderID("Kvist")

    // Suggestion computation is fully cooperative with coroutine cancellation (see
    // KvistCompletionClient), so the platform's own cancel-on-supersede behavior is
    // enough: there is nothing project-specific in the cache, so one instance for the
    // lifetime of this (application-level, platform-owned) provider is enough.
    private val client = KvistCompletionClient()

    override fun isEnabled(event: InlineCompletionEvent): Boolean {
        if (!KvistCompletionSettings.isEnabled) return false
        val documentChange = event as? InlineCompletionEvent.DocumentChange ?: return false
        return documentChange.editor.document.textLength <= MAX_DOCUMENT_CHARS
    }

    override suspend fun getDebounceDelay(request: InlineCompletionRequest): Duration = DEBOUNCE_MS.milliseconds

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

        val completion = try {
            client.requestCompletion(target, payload)
        }
        catch (exception: CancellationException) {
            throw exception
        }
        catch (exception: CompletionRequestException) {
            LOG.debug("Kvist inline completion failed: ${exception.message}")
            return InlineCompletionSuggestion.Empty
        }
        if (completion.isEmpty()) return InlineCompletionSuggestion.Empty

        return InlineCompletionSuggestion {
            variant {
                emit(InlineCompletionGrayTextElement(completion))
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

        // Idle-typing debounce: fast enough to feel responsive, slow enough that most
        // keystrokes never spawn a process at all.
        private const val DEBOUNCE_MS = 300L

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
