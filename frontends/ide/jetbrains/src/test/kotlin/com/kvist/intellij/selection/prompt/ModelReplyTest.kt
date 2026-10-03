package com.kvist.intellij.selection.prompt

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class ModelReplyTest {
    @Test
    fun fenceAroundTheWholeReplyIsRemoved() {
        assertEquals("val x = 1", ModelReply.replacementFor("val y = 2", "```kotlin\nval x = 1\n```"))
    }

    @Test
    fun codeWithoutAFenceIsLeftAlone() {
        assertEquals("val x = 1", ModelReply.replacementFor("val y = 2", "val x = 1"))
    }

    @Test
    fun selectionKeepsItsOwnLeadingAndTrailingNewlines() {
        assertEquals("\nnew\n", ModelReply.replacementFor("\nold\n", "new"))
        assertEquals("new", ModelReply.replacementFor("old", "\n\nnew\n\n"))
    }

    @Test
    fun windowsLineEndingsBecomeDocumentLineEndings() {
        assertEquals("a\nb", ModelReply.replacementFor("x", "a\r\nb"))
    }

    @Test
    fun blankReplyMeansNoReplacement() {
        assertNull(ModelReply.replacementFor("old", "  \n "))
        assertNull(ModelReply.replacementFor("old", "```\n\n```"))
    }

    @Test
    fun explainAndFixReportIsReadFromJson() {
        val report = ExplainAndFixReport.parse(
            """
            {"explanation": "Adds two numbers.", "risks": ["overflow"], "bugs": [],
             "patch": "return a + b", "patch_safety": "Same result for all inputs."}
            """.trimIndent()
        )

        assertNotNull(report)
        assertEquals("Adds two numbers.", report!!.explanation)
        assertEquals(listOf("overflow"), report.risks)
        assertEquals(emptyList<String>(), report.bugs)
        assertEquals("return a + b", report.patch)
        assertEquals("Same result for all inputs.", report.patchSafety)
    }

    @Test
    fun explainAndFixReportToleratesAFenceAndNullPatch() {
        val report = ExplainAndFixReport.parse(
            "```json\n{\"explanation\": \"Fine.\", \"risks\": [], \"bugs\": [], \"patch\": null, \"patch_safety\": null}\n```"
        )

        assertNotNull(report)
        assertEquals("Fine.", report!!.explanation)
        assertNull(report.patch)
        assertNull(report.patchSafety)
    }

    @Test
    fun explainAndFixReplyThatIsNotJsonBecomesTheExplanationWithoutAPatch() {
        val report = ExplainAndFixReport.parse("This function adds numbers.")

        assertEquals("This function adds numbers.", report!!.explanation)
        assertNull(report.patch)
    }

    @Test
    fun emptyExplainAndFixReplyIsNotAReport() {
        assertNull(ExplainAndFixReport.parse("   "))
        assertNull(ExplainAndFixReport.parse("{}"))
    }
}
