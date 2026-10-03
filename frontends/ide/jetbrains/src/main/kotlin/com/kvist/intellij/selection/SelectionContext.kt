package com.kvist.intellij.selection

import com.intellij.openapi.editor.Document
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.TextRange
import com.intellij.psi.PsiDocumentManager
import com.intellij.psi.PsiFile

/**
 * What the editor actions know about the user's selection when they start. Offsets and
 * [documentStamp] let the apply step detect that the document moved on while the model
 * was answering.
 */
data class SelectionContext(
    val selectedText: String,
    val selectionStart: Int,
    val selectionEnd: Int,
    val filePath: String,
    val languageId: String,
    val psiFile: PsiFile?,
    val textBefore: String,
    val textAfter: String,
    val projectName: String,
    val documentStamp: Long,
) {
    val surroundingText: String
        get() = textBefore + selectedText + textAfter
}

object SelectionContextService {
    private const val CONTEXT_LINES = 20
    private const val MAX_CONTEXT_CHARS = 2_000

    // Called from the action on the EDT, which holds the read access it needs.
    fun capture(project: Project, editor: Editor): SelectionContext? {
        val selectionModel = editor.selectionModel
        if (!selectionModel.hasSelection()) return null

        val start = selectionModel.selectionStart
        val end = selectionModel.selectionEnd
        val document = editor.document
        val selectedText = document.getText(TextRange(start, end))
        if (selectedText.isBlank()) return null

        val psiFile = PsiDocumentManager.getInstance(project).getPsiFile(document)
        val virtualFile = FileDocumentManager.getInstance().getFile(document)
        return SelectionContext(
            selectedText = selectedText,
            selectionStart = start,
            selectionEnd = end,
            filePath = displayPath(project, virtualFile?.path),
            languageId = psiFile?.language?.id ?: virtualFile?.extension.orEmpty(),
            psiFile = psiFile,
            textBefore = textBefore(document, start),
            textAfter = textAfter(document, end),
            projectName = project.name,
            documentStamp = document.modificationStamp,
        )
    }

    private fun displayPath(project: Project, absolutePath: String?): String {
        if (absolutePath == null) return "untitled"
        val basePath = project.basePath ?: return absolutePath
        val prefix = "$basePath/"
        return if (absolutePath.startsWith(prefix)) absolutePath.removePrefix(prefix) else absolutePath
    }

    private fun textBefore(document: Document, start: Int): String {
        val firstLine = (document.getLineNumber(start) - CONTEXT_LINES).coerceAtLeast(0)
        val from = maxOf(document.getLineStartOffset(firstLine), start - MAX_CONTEXT_CHARS)
        return document.getText(TextRange(from, start))
    }

    private fun textAfter(document: Document, end: Int): String {
        val lastLine = (document.getLineNumber(end) + CONTEXT_LINES).coerceAtMost(document.lineCount - 1)
        val to = minOf(document.getLineEndOffset(lastLine), end + MAX_CONTEXT_CHARS)
        return document.getText(TextRange(end, to))
    }
}
