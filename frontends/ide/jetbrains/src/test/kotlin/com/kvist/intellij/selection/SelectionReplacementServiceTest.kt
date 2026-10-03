package com.kvist.intellij.selection

import com.intellij.openapi.command.undo.UndoManager
import com.intellij.openapi.fileEditor.impl.text.TextEditorProvider
import com.intellij.testFramework.fixtures.BasePlatformTestCase

class SelectionReplacementServiceTest : BasePlatformTestCase() {
    private fun captureSelection(text: String): SelectionContext {
        val start = myFixture.editor.document.text.indexOf(text)
        myFixture.editor.selectionModel.setSelection(start, start + text.length)
        return SelectionContextService.capture(project, myFixture.editor)!!
    }

    fun testReplacesOnlyTheSelection() {
        myFixture.configureByText("Sample.txt", "before TARGET after\n")
        val context = captureSelection("TARGET")

        val result = SelectionReplacementService.replace(project, myFixture.editor.document, context, "done", "Test replace")

        assertEquals(ReplacementResult.APPLIED, result)
        assertEquals("before done after\n", myFixture.editor.document.text)
    }

    fun testUndoRestoresTheSelectionTextInOneStep() {
        myFixture.configureByText("Sample.txt", "before TARGET after\n")
        val context = captureSelection("TARGET")
        SelectionReplacementService.replace(project, myFixture.editor.document, context, "done", "Test replace")

        val fileEditor = TextEditorProvider.getInstance().getTextEditor(myFixture.editor)
        val undoManager = UndoManager.getInstance(project)
        assertTrue(undoManager.isUndoAvailable(fileEditor))
        undoManager.undo(fileEditor)

        assertEquals("before TARGET after\n", myFixture.editor.document.text)
    }

    fun testRefusesWhenTheDocumentChangedAfterCapture() {
        myFixture.configureByText("Sample.txt", "before TARGET after\n")
        val context = captureSelection("TARGET")
        myFixture.type("x")

        val result = SelectionReplacementService.replace(project, myFixture.editor.document, context, "done", "Test replace")

        assertEquals(ReplacementResult.DOCUMENT_CHANGED, result)
        assertFalse(myFixture.editor.document.text.contains("done"))
    }

    fun testRefusesWhenTheRangeNoLongerHoldsTheSelectedText() {
        myFixture.configureByText("Sample.txt", "before TARGET after\n")
        val context = captureSelection("TARGET")
        // Same stamp as captured, but a range that points at different text.
        val moved = context.copy(selectedText = "OTHER")

        assertFalse(SelectionReplacementService.isCurrent(myFixture.editor.document, moved))
        assertEquals(
            ReplacementResult.DOCUMENT_CHANGED,
            SelectionReplacementService.replace(project, myFixture.editor.document, moved, "done", "Test replace"),
        )
        assertEquals("before TARGET after\n", myFixture.editor.document.text)
    }

    fun testRefusesWhenTheRangeIsPastTheEndOfTheDocument() {
        myFixture.configureByText("Sample.txt", "before TARGET after\n")
        val context = captureSelection("TARGET").copy(selectionEnd = 10_000)

        assertFalse(SelectionReplacementService.isCurrent(myFixture.editor.document, context))
    }
}
