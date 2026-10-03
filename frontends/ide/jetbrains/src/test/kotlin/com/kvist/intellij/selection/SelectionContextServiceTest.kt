package com.kvist.intellij.selection

import com.intellij.testFramework.fixtures.BasePlatformTestCase

class SelectionContextServiceTest : BasePlatformTestCase() {
    private val source = (1..60).joinToString("\n") { "line $it" } + "\n"

    private fun select(text: String) {
        val start = source.indexOf(text)
        myFixture.editor.selectionModel.setSelection(start, start + text.length)
    }

    fun testNoSelectionGivesNoContext() {
        myFixture.configureByText("Sample.txt", source)

        assertNull(SelectionContextService.capture(project, myFixture.editor))
    }

    fun testBlankSelectionGivesNoContext() {
        myFixture.configureByText("Sample.txt", "a   b\n")
        myFixture.editor.selectionModel.setSelection(1, 4)

        assertNull(SelectionContextService.capture(project, myFixture.editor))
    }

    fun testCapturesTheSelectionAndItsFile() {
        myFixture.configureByText("Sample.txt", source)
        select("line 30")

        val context = SelectionContextService.capture(project, myFixture.editor)!!

        assertEquals("line 30", context.selectedText)
        assertEquals(source.indexOf("line 30"), context.selectionStart)
        assertEquals(context.selectionStart + "line 30".length, context.selectionEnd)
        assertTrue(context.filePath.endsWith("Sample.txt"))
        assertEquals("TEXT", context.languageId)
        assertNotNull(context.psiFile)
        assertEquals(project.name, context.projectName)
        assertEquals(myFixture.editor.document.modificationStamp, context.documentStamp)
    }

    fun testSurroundingTextIsABoundedWindowAroundTheSelection() {
        myFixture.configureByText("Sample.txt", source)
        select("line 30")

        val context = SelectionContextService.capture(project, myFixture.editor)!!

        assertTrue(context.textBefore.startsWith("line 10\n"))
        assertTrue(context.textBefore.endsWith("line 29\n"))
        assertTrue(context.textAfter.startsWith("\nline 31"))
        assertTrue(context.textAfter.endsWith("line 50"))
        assertFalse(context.surroundingText.contains("line 9\n"))
        assertFalse(context.surroundingText.contains("line 51"))
        assertTrue(context.surroundingText.contains("line 30"))
    }

    fun testSurroundingTextStopsAtTheEdgesOfTheDocument() {
        myFixture.configureByText("Sample.txt", "first\nsecond\n")
        myFixture.editor.selectionModel.setSelection(0, 5)

        val context = SelectionContextService.capture(project, myFixture.editor)!!

        assertEquals("", context.textBefore)
        assertEquals("\nsecond\n", context.textAfter)
    }
}
