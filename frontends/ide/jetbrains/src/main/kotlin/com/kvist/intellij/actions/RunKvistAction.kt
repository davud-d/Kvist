package com.kvist.intellij.actions

import com.kvist.intellij.editor.KvistPage
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.DumbAwareAction

class RunKvistAction : DumbAwareAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = event.project?.let { !it.isDisposed } == true
    }

    override fun actionPerformed(event: AnActionEvent) {
        event.project?.let { KvistPage.open(it) }
    }
}
