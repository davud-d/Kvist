package com.kvist.intellij.completion

import com.intellij.codeInsight.inline.completion.InlineCompletionInsertEnvironment
import com.intellij.codeInsight.inline.completion.InlineCompletionInsertHandler
import com.intellij.codeInsight.inline.completion.elements.InlineCompletionElement
import com.intellij.codeInsight.inline.completion.elements.InlineCompletionGrayTextElement
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.markup.EffectType
import com.intellij.openapi.editor.markup.HighlighterLayer
import com.intellij.openapi.editor.markup.HighlighterTargetArea
import com.intellij.openapi.editor.markup.RangeHighlighter
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.util.Disposer
import com.intellij.ui.JBColor
import java.awt.Color
import java.awt.Font
import java.awt.Rectangle

/**
 * New text that takes the place of the span from [before] characters left of the caret to [after] characters right
 * of it (either may be negative: the span lies wholly on one side, elsewhere on the caret's line).
 *
 * The platform has no element for a deletion: it inserts the text of every element at the caret and then calls
 * the provider's insert handler. So the element is the new text, drawn as ghost text at the caret, with the old
 * text struck through while the suggestion is shown; [KvistReplacementInsertHandler] puts the new text in the
 * old text's place on accept.
 */
class KvistReplacementElement(
    override val text: String,
    val before: Int,
    val after: Int,
) : InlineCompletionElement {
    override fun toPresentable(): InlineCompletionElement.Presentable = Presentable(this)

    private class Presentable(override val element: KvistReplacementElement) : InlineCompletionElement.Presentable {
        private val ghost = InlineCompletionGrayTextElement(element.text).toPresentable()
        private var struck: RangeHighlighter? = null
        private var struckIn: Editor? = null

        override fun isVisible(): Boolean = ghost.isVisible()

        override fun render(editor: Editor, offset: Int) {
            ghost.render(editor, offset)
            removeStrike()
            val length = editor.document.textLength
            val start = (offset - element.before).coerceIn(0, length)
            val end = (offset + element.after).coerceIn(start, length)
            struck = editor.markupModel.addRangeHighlighter(
                start, end, HighlighterLayer.SELECTION - 1, STRUCK, HighlighterTargetArea.EXACT_RANGE
            )
            struckIn = editor
        }

        override fun getBounds(): Rectangle? = ghost.getBounds()

        override fun startOffset(): Int? = ghost.startOffset()

        override fun endOffset(): Int? = ghost.endOffset()

        override fun dispose() {
            removeStrike()
            Disposer.dispose(ghost)
        }

        private fun removeStrike() {
            struck?.let { struckIn?.markupModel?.removeHighlighter(it) }
            struck = null
            struckIn = null
        }
    }

    private companion object {
        val STRUCK = TextAttributes(
            null,
            JBColor(Color(255, 0, 0, 40), Color(255, 82, 82, 70)),
            JBColor(Color(200, 40, 40), Color(255, 110, 110)),
            EffectType.STRIKEOUT,
            Font.PLAIN,
        )
    }
}

/** The platform has put the new text at the caret; it belongs where the old text is, so it moves there. */
class KvistReplacementInsertHandler : InlineCompletionInsertHandler {
    override fun afterInsertion(environment: InlineCompletionInsertEnvironment, elements: List<InlineCompletionElement>) {
        val edit = elements.filterIsInstance<KvistReplacementElement>().firstOrNull() ?: return
        val editor = environment.editor
        val document = editor.document
        val caret = environment.insertedRange.startOffset
        document.deleteString(caret, environment.insertedRange.endOffset)
        val start = (caret - edit.before).coerceIn(0, document.textLength)
        val end = (caret + edit.after).coerceIn(start, document.textLength)
        document.replaceString(start, end, edit.text)
        editor.caretModel.moveToOffset(
            when {
                caret in start..end -> start + edit.text.length
                end < caret -> caret + edit.text.length - (end - start)
                else -> caret
            }
        )
    }
}
