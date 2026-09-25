package com.kvist.intellij.actions

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.project.DumbAwareToggleAction
import com.kvist.intellij.completion.KvistCompletionSettings

/** Tools-menu on/off switch for Kvist's inline (ghost text) completion. */
class ToggleInlineCompletionAction : DumbAwareToggleAction() {
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun isSelected(event: AnActionEvent): Boolean = KvistCompletionSettings.isEnabled

    override fun setSelected(event: AnActionEvent, state: Boolean) {
        KvistCompletionSettings.isEnabled = state
    }
}
