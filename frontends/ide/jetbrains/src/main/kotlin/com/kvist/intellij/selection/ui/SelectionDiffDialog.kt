package com.kvist.intellij.selection.ui

import com.intellij.diff.DiffContentFactory
import com.intellij.diff.DiffManager
import com.intellij.diff.requests.SimpleDiffRequest
import com.intellij.openapi.fileTypes.FileType
import com.intellij.openapi.fileTypes.PlainTextFileType
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.DialogWrapper
import com.intellij.util.ui.JBUI
import javax.swing.JComponent

/** Shows the selection next to the proposed text; OK ("Apply") is the only way a change goes through. */
class SelectionDiffDialog(
    private val project: Project,
    dialogTitle: String,
    private val original: String,
    private val proposed: String,
    private val fileType: FileType?,
) : DialogWrapper(project, true) {

    init {
        title = dialogTitle
        setOKButtonText("Apply")
        init()
    }

    override fun createCenterPanel(): JComponent {
        val factory = DiffContentFactory.getInstance()
        val type = fileType ?: PlainTextFileType.INSTANCE
        val request = SimpleDiffRequest(
            title,
            factory.create(project, original, type),
            factory.create(project, proposed, type),
            "Current selection",
            "Kvist suggestion",
        )
        val panel = DiffManager.getInstance().createRequestPanel(project, disposable, window)
        panel.setRequest(request)
        return panel.component.apply { preferredSize = JBUI.size(900, 520) }
    }

    override fun getDimensionServiceKey(): String = "com.kvist.intellij.selection.SelectionDiffDialog"
}
