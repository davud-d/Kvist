package com.kvist.intellij.selection.ui

import com.intellij.openapi.editor.Editor
import com.intellij.openapi.project.Project
import com.kvist.intellij.selection.ReplacementResult
import com.kvist.intellij.selection.SelectionContext
import com.kvist.intellij.selection.SelectionReplacementService
import com.kvist.intellij.ui.KvistNotifications

/** Preview first, apply only on confirmation: the one path by which every Kvist editor action edits the document. */
object SelectionEditFlow {

    // Must run on the EDT.
    fun previewAndApply(
        project: Project,
        editor: Editor,
        context: SelectionContext,
        replacement: String,
        commandName: String,
    ) {
        if (editor.isDisposed) return
        val document = editor.document
        if (!SelectionReplacementService.isCurrent(document, context)) {
            KvistNotifications.error(project, "Document changed, retry action.")
            return
        }
        if (replacement == context.selectedText) {
            KvistNotifications.info(project, "Kvist suggested no changes.")
            return
        }

        val dialog = SelectionDiffDialog(project, commandName, context.selectedText, replacement, context.psiFile?.fileType)
        if (!dialog.showAndGet()) return

        when (SelectionReplacementService.replace(project, document, context, replacement, commandName)) {
            ReplacementResult.APPLIED -> selectReplacement(editor, context, replacement)
            ReplacementResult.DOCUMENT_CHANGED -> KvistNotifications.error(project, "Document changed, retry action.")
            ReplacementResult.FAILED -> KvistNotifications.error(project, "Failed to apply patch.")
        }
    }

    private fun selectReplacement(editor: Editor, context: SelectionContext, replacement: String) {
        if (editor.isDisposed) return
        editor.selectionModel.setSelection(context.selectionStart, context.selectionStart + replacement.length)
    }
}
