package com.kvist.intellij.chat

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.util.Disposer
import com.intellij.testFramework.LightVirtualFile
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.kvist.intellij.editor.KvistPage
import com.kvist.intellij.editor.KvistPageEditorProvider

class KvistPageTest : BasePlatformTestCase() {
    fun testOpeningTwiceReusesEditorAndCanReopenAfterClosing() {
        val manager = FileEditorManager.getInstance(project)
        KvistPage.open(project)
        val file = manager.openFiles.single()
        assertEquals("Kvist", file.name)
        assertFalse(file.isWritable)
        val provider = KvistPageEditorProvider()
        assertTrue(provider.accept(project, file))
        // The headless FileEditorManager installs its text editor regardless of providers.
        val editor = provider.createEditor(project, file)
        try {
            assertEquals("Kvist", editor.name)
            assertSame(file, editor.file)
            assertFalse(editor.isModified)
            assertNotNull(editor.component)
        }
        finally {
            Disposer.dispose(editor)
        }

        KvistPage.open(project)
        assertEquals(listOf(file), manager.openFiles.toList())

        manager.closeFile(file)
        assertEmpty(manager.openFiles.toList())
        KvistPage.open(project)
        assertEquals(1, manager.openFiles.size)
        manager.openFiles.forEach { manager.closeFile(it) }
    }

    fun testProviderDoesNotInterceptOrdinaryFiles() {
        assertFalse(KvistPageEditorProvider().accept(project, LightVirtualFile("Kvist", "source")))
    }
}
