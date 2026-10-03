package com.kvist.intellij.actions

import com.intellij.openapi.components.service
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.project.Project
import com.kvist.intellij.selection.KvistSelectionTaskRunner
import com.kvist.intellij.selection.SelectionContext
import com.kvist.intellij.selection.prompt.ExplainAndFixPromptBuilder
import com.kvist.intellij.selection.prompt.ExplainAndFixReport
import com.kvist.intellij.selection.prompt.ModelReply
import com.kvist.intellij.selection.ui.ExplainAndFixDialog
import com.kvist.intellij.selection.ui.SelectionEditFlow
import com.kvist.intellij.ui.KvistNotifications

class ExplainAndFixAction : KvistSelectionAction() {
    override fun perform(project: Project, editor: Editor, context: SelectionContext) {
        val request = ExplainAndFixPromptBuilder.build(context)
        project.service<KvistSelectionTaskRunner>().run("Kvist: reviewing selection", request) { reply ->
            val report = ExplainAndFixReport.parse(reply)
            if (report == null) {
                KvistNotifications.error(project, "Empty model response.")
                return@run
            }
            showReport(project, editor, context, report)
        }
    }

    private fun showReport(project: Project, editor: Editor, context: SelectionContext, report: ExplainAndFixReport) {
        // A patch identical to the selection changes nothing, so it is not offered.
        val patch = report.patch
            ?.let { ModelReply.replacementFor(context.selectedText, it) }
            ?.takeIf { it != context.selectedText }

        val dialog = ExplainAndFixDialog(project, report, hasPatch = patch != null)
        if (!dialog.showAndGet() || patch == null) return

        SelectionEditFlow.previewAndApply(project, editor, context, patch, COMMAND_NAME)
    }

    private companion object {
        const val COMMAND_NAME = "Kvist: Apply Patch"
    }
}
