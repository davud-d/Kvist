package com.kvist.intellij.actions

import com.intellij.openapi.editor.Editor
import com.intellij.openapi.project.Project
import com.kvist.intellij.selection.SelectionContext
import com.kvist.intellij.selection.prompt.RewriteAsSeniorPromptBuilder

class RewriteAsSeniorAction : KvistSelectionAction() {
    override fun perform(project: Project, editor: Editor, context: SelectionContext) {
        requestReplacement(
            project,
            editor,
            context,
            progressTitle = "Kvist: rewriting selection",
            request = RewriteAsSeniorPromptBuilder.build(context),
            commandName = "Kvist: Rewrite as Senior",
        )
    }
}
