package com.kvist.intellij.editor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.fileEditor.FileEditorPolicy
import com.intellij.openapi.fileEditor.FileEditorProvider
import com.intellij.openapi.fileEditor.FileEditorState
import com.intellij.openapi.fileEditor.ex.FileEditorManagerEx
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.util.UserDataHolderBase
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.ui.components.JBLabel
import com.intellij.ui.jcef.JBCefApp
import com.kvist.intellij.chat.KvistChatPanel
import java.beans.PropertyChangeListener
import javax.swing.JComponent

class KvistPageEditorProvider : FileEditorProvider, DumbAware {
    override fun accept(project: Project, file: VirtualFile) = file is KvistPageFile || file is KvistBrowserFile
    override fun createEditor(project: Project, file: VirtualFile): FileEditor = KvistPageEditor(project, file)
    override fun getEditorTypeId() = "kvist-web-page"
    override fun getPolicy() = FileEditorPolicy.HIDE_DEFAULT_EDITOR
}

private class KvistPageEditor(private val project: Project, private val pageFile: VirtualFile) : UserDataHolderBase(), FileEditor {
    private val view: JComponent = createView()

    private fun createView(): JComponent {
        try {
            if (!JBCefApp.isSupported()) {
                return JBLabel("Kvist requires an IDE runtime with JCEF. / Для Kvist требуется JCEF.")
            }
            if (pageFile is KvistBrowserFile) {
                val browser = com.intellij.ui.jcef.JBCefBrowser(pageFile.viewerUrl)
                Disposer.register(this, browser)
                return browser.component
            }
            val panel = KvistChatPanel(project, workspacePage = true, onSessionTitle = ::showSessionTitle)
            Disposer.register(this, panel)
            return panel.component
        }
        catch (error: NoClassDefFoundError) {
            return JBLabel("Enable Web Browser (JCEF) in Settings | Plugins and restart the IDE. / Включите плагин JCEF.")
        }
    }

    private fun showSessionTitle(title: String) {
        val file = pageFile as? KvistPageFile ?: return
        if (file.sessionTitle == title) return
        file.sessionTitle = title
        ApplicationManager.getApplication().invokeLater({ FileEditorManagerEx.getInstanceEx(project).updateFilePresentation(file) }, project.disposed)
    }

    override fun getComponent() = view
    override fun getPreferredFocusedComponent() = view
    override fun getName() = "Kvist"
    override fun getFile() = pageFile
    override fun setState(state: FileEditorState) = Unit
    override fun isModified() = false
    override fun isValid() = pageFile.isValid
    override fun addPropertyChangeListener(listener: PropertyChangeListener) = Unit
    override fun removePropertyChangeListener(listener: PropertyChangeListener) = Unit
    override fun dispose() = Unit
}
