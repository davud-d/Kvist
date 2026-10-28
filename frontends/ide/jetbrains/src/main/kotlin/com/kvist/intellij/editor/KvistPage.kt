package com.kvist.intellij.editor

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.ex.FileEditorManagerEx
import com.intellij.openapi.project.Project
import com.intellij.testFramework.LightVirtualFile

internal class KvistPageFile : LightVirtualFile("Kvist", KvistPageFileType, "") {
    // The name of the session on screen, for the tab title; written from the page, read by the title provider.
    @Volatile
    var sessionTitle: String = ""

    init {
        isWritable = false
    }
}

object KvistPage {
    fun open(project: Project) {
        val manager = FileEditorManager.getInstance(project)
        val file = manager.openFiles.filterIsInstance<KvistPageFile>().firstOrNull() ?: KvistPageFile()
        manager.openFile(file, true)
        // The tab starts with the icon its name implies; this makes the IDE ask the file itself again.
        FileEditorManagerEx.getInstanceEx(project).updateFilePresentation(file)
    }
}
