package com.kvist.intellij.actions

import com.kvist.intellij.runtime.KvistLauncher
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.DumbAwareAction

class RunKvistAction : DumbAwareAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = KvistLauncher.isLaunchContextAvailable(event.project)
    }

    override fun actionPerformed(event: AnActionEvent) {
        KvistLauncher.launch(event.project)
    }
}