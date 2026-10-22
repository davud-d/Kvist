package com.kvist.intellij.completion

import com.intellij.codeInsight.inline.completion.InlineCompletionInsertEnvironment
import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.editor.markup.EffectType
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.util.TextRange
import com.intellij.testFramework.fixtures.BasePlatformTestCase

class KvistReplacementElementTest : BasePlatformTestCase() {
    private fun accept(text: String, edit: KvistReplacementElement): String {
        myFixture.configureByText("a.txt", text)
        val caret = myFixture.editor.caretModel.offset
        // What the platform does before it calls the insert handler: the new text goes in at the caret.
        WriteCommandAction.runWriteCommandAction(project) {
            myFixture.editor.document.insertString(caret, edit.text)
            val environment = InlineCompletionInsertEnvironment(
                myFixture.editor, myFixture.file, TextRange.from(caret, edit.text.length), null
            )
            KvistReplacementInsertHandler().afterInsertion(environment, listOf(edit))
        }
        return myFixture.editor.document.text
    }

    fun testAcceptingReplacesTheSpanAroundTheCaret() {
        val result = accept("# Gradle saz<caret>cache\n.gradle", KvistReplacementElement("cache\n.gradle", before = 3, after = 5))
        assertEquals("# Gradle cache\n.gradle\n.gradle", result)
        assertEquals("# Gradle cache\n.gradle".length, myFixture.editor.caretModel.offset)
    }

    fun testAcceptingFixesAWordLeftOfTheCaretAndKeepsTheCaretInPlace() {
        val result = accept("Copyrigth (c) 2026<caret> tail", KvistReplacementElement("Copyright", before = 18, after = -9))
        assertEquals("Copyright (c) 2026 tail", result)
        assertEquals("Copyright (c) 2026".length, myFixture.editor.caretModel.offset)
    }

    fun testAcceptingFixesAWordRightOfTheCaret() {
        val result = accept("x = <caret> retrun y", KvistReplacementElement("return", before = -1, after = 7))
        assertEquals("x =  return y", result)
        assertEquals("x = ".length, myFixture.editor.caretModel.offset)
    }

    fun testOldTextIsStruckThroughWhileShownAndClearedWithTheSuggestion() {
        myFixture.configureByText("a.txt", "# Gradle saz<caret>cache")
        val presentable = KvistReplacementElement("cache", before = 3, after = 5).toPresentable()
        presentable.render(myFixture.editor, myFixture.editor.caretModel.offset)

        val struck = myFixture.editor.markupModel.allHighlighters.single { it.getTextAttributes(null)?.effectType == EffectType.STRIKEOUT }
        assertEquals(TextRange(9, 17), TextRange(struck.startOffset, struck.endOffset))

        Disposer.dispose(presentable)
        assertTrue(myFixture.editor.markupModel.allHighlighters.none { it.getTextAttributes(null)?.effectType == EffectType.STRIKEOUT })
    }
}
