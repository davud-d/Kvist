package com.kvist.intellij.actions

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.ui.popup.JBPopupFactory
import com.kvist.intellij.selection.SelectionContext
import com.kvist.intellij.selection.prompt.TranslateSelectionPromptBuilder

class TranslateSelectionAction : KvistSelectionAction() {
    override fun perform(project: Project, editor: Editor, context: SelectionContext) {
        JBPopupFactory.getInstance()
            .createPopupChooserBuilder(LANGUAGES + CUSTOM_LANGUAGE)
            .setTitle("Translate Selection To")
            .setItemChosenCallback { choice ->
                if (choice == CUSTOM_LANGUAGE) {
                    // The popup is still closing; a modal dialog opened inside its callback would fight it for focus.
                    ApplicationManager.getApplication().invokeLater({ askCustomLanguage(project, editor, context) }, project.disposed)
                }
                else {
                    translate(project, editor, context, choice)
                }
            }
            .createPopup()
            .showInBestPositionFor(editor)
    }

    private fun askCustomLanguage(project: Project, editor: Editor, context: SelectionContext) {
        val language = Messages.showInputDialog(project, "Language name:", "Kvist: Custom Language", null)
            ?.trim()
            .orEmpty()
        if (language.isNotEmpty()) {
            translate(project, editor, context, language)
        }
    }

    private fun translate(project: Project, editor: Editor, context: SelectionContext, language: String) {
        requestReplacement(
            project,
            editor,
            context,
            progressTitle = "Kvist: translating selection",
            request = TranslateSelectionPromptBuilder.build(context, language),
            commandName = "Kvist: Translate Selection",
        )
    }

    private companion object {
        const val CUSTOM_LANGUAGE = "Custom language…"
        val LANGUAGES = listOf("English", "Russian", "Kazakh", "Kyrgyz", "Arabic", "British English", "American English")
    }
}
