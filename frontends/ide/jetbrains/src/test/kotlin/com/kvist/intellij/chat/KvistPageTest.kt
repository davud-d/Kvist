package com.kvist.intellij.chat

import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.util.Disposer
import com.intellij.testFramework.LightVirtualFile
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.kvist.intellij.editor.KvistPage
import com.kvist.intellij.editor.KvistPageEditorProvider
import com.kvist.intellij.editor.KvistPageFileType
import com.kvist.intellij.editor.KvistPageIconProvider

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

    fun testPageTabShowsTheKvistIconAndOrdinaryFilesKeepTheirs() {
        KvistPage.open(project)
        val file = FileEditorManager.getInstance(project).openFiles.single()
        val provider = KvistPageIconProvider()
        assertNotNull(provider.getIcon(file, 0, project))
        // The path the editor tab and the breadcrumbs take, not the provider on its own.
        assertSame(KvistPageFileType.icon, com.intellij.util.IconUtil.getIcon(file, 0, project))
        assertSame(KvistPageFileType.icon, file.fileType.icon)
        // What an icon pack reaches through the PSI file, ahead of everything else.
        val psiFile = com.intellij.psi.PsiManager.getInstance(project).findFile(file)
        assertNotNull(psiFile)
        assertSame(KvistPageFileType.icon, com.intellij.ide.IconProvider.EXTENSION_POINT_NAME.extensionList.firstNotNullOfOrNull { it.getIcon(psiFile!!, 0) })
        // The tab shows an icon chosen by the file's name first, before it has asked anything else.
        assertSame(KvistPageFileType.icon, com.intellij.openapi.fileTypes.FileTypeManager.getInstance().getFileTypeByFileName("Kvist").icon)
        assertNull(provider.getIcon(LightVirtualFile("Kvist", "source"), 0, project))
        FileEditorManager.getInstance(project).closeFile(file)
    }

    fun testTabTitleFollowsTheSessionName() {
        KvistPage.open(project)
        val file = FileEditorManager.getInstance(project).openFiles.single() as com.kvist.intellij.editor.KvistPageFile
        val titles = com.kvist.intellij.editor.KvistPageTabTitleProvider()
        assertNull(titles.getEditorTabTitle(project, file))
        file.sessionTitle = "  Run the tests  "
        assertEquals("Kvist | Run the tests", titles.getEditorTabTitle(project, file))
        file.sessionTitle = "x".repeat(100)
        assertTrue(titles.getEditorTabTitle(project, file)!!.length <= "Kvist | ".length + 40)
        assertNull(titles.getEditorTabTitle(project, LightVirtualFile("Kvist", "source")))
        FileEditorManager.getInstance(project).closeFile(file)
    }

    fun testProviderDoesNotInterceptOrdinaryFiles() {
        assertFalse(KvistPageEditorProvider().accept(project, LightVirtualFile("Kvist", "source")))
    }
}
