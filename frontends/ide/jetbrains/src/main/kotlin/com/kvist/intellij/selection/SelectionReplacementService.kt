package com.kvist.intellij.selection

import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.editor.Document
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.progress.ProcessCanceledException
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.TextRange

enum class ReplacementResult {
    APPLIED,

    /** The document moved on since the selection was captured. */
    DOCUMENT_CHANGED,
    FAILED,
}

/**
 * Replaces the captured selection, and only it. The edit is one write command, so the
 * IDE's own Undo reverts it in a single step.
 */
object SelectionReplacementService {
    private val LOG = logger<SelectionReplacementService>()

    /** True when the selection's range still holds the text it was captured with. */
    fun isCurrent(document: Document, context: SelectionContext): Boolean {
        if (document.modificationStamp != context.documentStamp) return false
        if (context.selectionEnd > document.textLength) return false
        val current = document.getText(TextRange(context.selectionStart, context.selectionEnd))
        return current == context.selectedText
    }

    // Must run on the EDT, like any document write.
    fun replace(
        project: Project,
        document: Document,
        context: SelectionContext,
        replacement: String,
        commandName: String,
    ): ReplacementResult {
        if (!isCurrent(document, context)) return ReplacementResult.DOCUMENT_CHANGED
        if (!FileDocumentManager.getInstance().requestWriting(document, project)) return ReplacementResult.FAILED

        return try {
            WriteCommandAction.writeCommandAction(project, *listOfNotNull(context.psiFile).toTypedArray())
                .withName(commandName)
                .run<RuntimeException> {
                    document.replaceString(context.selectionStart, context.selectionEnd, replacement)
                }
            ReplacementResult.APPLIED
        }
        catch (exception: ProcessCanceledException) {
            throw exception
        }
        catch (exception: RuntimeException) {
            LOG.warn("Failed to replace the selection in the document", exception)
            ReplacementResult.FAILED
        }
    }
}
