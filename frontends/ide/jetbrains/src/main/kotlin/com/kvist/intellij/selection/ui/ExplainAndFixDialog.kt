package com.kvist.intellij.selection.ui

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.components.JBTextArea
import com.intellij.util.ui.JBUI
import com.kvist.intellij.selection.prompt.ExplainAndFixReport
import javax.swing.Action
import javax.swing.JComponent

/**
 * Shows the explanation, risks, bugs and the reason a patch is safe. When there is a patch,
 * "Apply Patch…" closes the dialog with OK and the caller continues into the diff preview;
 * nothing changes in the editor from here.
 */
class ExplainAndFixDialog(
    project: Project,
    private val report: ExplainAndFixReport,
    private val hasPatch: Boolean,
) : DialogWrapper(project, true) {

    init {
        title = "Kvist: Explain and Fix"
        setOKButtonText("Apply Patch…")
        setCancelButtonText("Close")
        init()
    }

    override fun createActions(): Array<Action> {
        return if (hasPatch) arrayOf(okAction, cancelAction) else arrayOf(cancelAction)
    }

    override fun createCenterPanel(): JComponent {
        val text = JBTextArea(renderReport(report, hasPatch)).apply {
            isEditable = false
            lineWrap = true
            wrapStyleWord = true
            border = JBUI.Borders.empty(8)
            caretPosition = 0
        }
        return JBScrollPane(text).apply { preferredSize = JBUI.size(720, 460) }
    }

    override fun getDimensionServiceKey(): String = "com.kvist.intellij.selection.ExplainAndFixDialog"

    companion object {
        fun renderReport(report: ExplainAndFixReport, hasPatch: Boolean): String = buildString {
            section("Explanation", report.explanation.ifBlank { "No explanation returned." })
            section("Risks", bulletList(report.risks))
            section("Bugs", bulletList(report.bugs))
            if (hasPatch) {
                section("Why the patch is safe", report.patchSafety ?: "The model gave no reason.")
                append("A safe patch is available. Apply Patch… shows a diff first; nothing changes until you confirm.")
            }
            else {
                append("No safe patch was proposed. The code is unchanged.")
            }
        }

        private fun StringBuilder.section(heading: String, body: String) {
            append(heading).append('\n').append(body).append("\n\n")
        }

        private fun bulletList(items: List<String>): String {
            return if (items.isEmpty()) "None found." else items.joinToString("\n") { "• $it" }
        }
    }
}
