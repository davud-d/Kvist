package com.kvist.intellij.editor

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.testFramework.LightVirtualFile
import java.net.URI

internal class KvistBrowserFile(val viewerUrl: String) : LightVirtualFile("Kvist Browser", KvistPageFileType, "") {
    init { isWritable = false }
}

internal object KvistBrowser {
    fun open(project: Project, url: String) {
        val uri = try { URI(url) } catch (_: Exception) { return }
        // Only the authenticated local screen viewer belongs in this editor.
        if (uri.scheme != "http" || uri.host != "127.0.0.1" || uri.port !in 1..65535 ||
            uri.path != "/" || uri.userInfo != null ||
            uri.rawQuery?.split("&")?.any { it.startsWith("t=") && it.length > 2 } != true) return
        val manager = FileEditorManager.getInstance(project)
        val file = manager.openFiles.filterIsInstance<KvistBrowserFile>().firstOrNull { it.viewerUrl == url }
            ?: KvistBrowserFile(url)
        manager.openFile(file, true)
    }
}
