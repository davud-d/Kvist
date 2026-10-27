package com.kvist.intellij.editor

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.testFramework.LightVirtualFile

internal class KvistPageFile : LightVirtualFile("Kvist") {
    init {
        isWritable = false
    }
}

object KvistPage {
    fun open(project: Project) {
        val manager = FileEditorManager.getInstance(project)
        val file = manager.openFiles.filterIsInstance<KvistPageFile>().firstOrNull() ?: KvistPageFile()
        manager.openFile(file, true)
    }
}
