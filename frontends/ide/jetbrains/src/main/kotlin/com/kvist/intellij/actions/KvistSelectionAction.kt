package com.kvist.intellij.actions

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.components.service
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.kvist.intellij.selection.KvistSelectionTaskRunner
import com.kvist.intellij.selection.SelectionContext
import com.kvist.intellij.selection.SelectionContextService
import com.kvist.intellij.selection.llm.KvistLlmRequest
import com.kvist.intellij.selection.prompt.ModelReply
import com.kvist.intellij.selection.ui.SelectionEditFlow
import com.kvist.intellij.ui.KvistNotifications

/**
 * Shared shell of the Kvist editor-menu actions: available when an editor with a selection is
 * in the context, and hands [perform] the captured selection. Anything beyond that belongs to
 * the services behind the subclasses.
 */
abstract class KvistSelectionAction : DumbAwareAction() {
    // update() only reads the editor's selection state through data keys, never Swing components.
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        val editor = event.getData(CommonDataKeys.EDITOR)
        if (event.project == null || editor == null) {
            event.presentation.isEnabledAndVisible = false
            return
        }
        event.presentation.isVisible = true
        event.presentation.isEnabled = !editor.isViewer && editor.selectionModel.hasSelection()
    }

    final override fun actionPerformed(event: AnActionEvent) {
        val project = event.project ?: return
        val editor = event.getData(CommonDataKeys.EDITOR) ?: return
        val context = SelectionContextService.capture(project, editor)
        if (context == null) {
            KvistNotifications.error(project, "No selection.")
            return
        }
        perform(project, editor, context)
    }

    protected abstract fun perform(project: Project, editor: Editor, context: SelectionContext)

    /** Asks the model for a replacement of the selection and offers it through a diff preview. */
    protected fun requestReplacement(
        project: Project,
        editor: Editor,
        context: SelectionContext,
        progressTitle: String,
        request: KvistLlmRequest,
        commandName: String,
    ) {
        project.service<KvistSelectionTaskRunner>().run(progressTitle, request) { reply ->
            val replacement = ModelReply.replacementFor(context.selectedText, reply)
            if (replacement == null) {
                KvistNotifications.error(project, "Empty model response.")
                return@run
            }
            SelectionEditFlow.previewAndApply(project, editor, context, replacement, commandName)
        }
    }
}
