package com.kvist.intellij.actions

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.actionSystem.DefaultActionGroup
import com.intellij.openapi.actionSystem.impl.SimpleDataContext
import com.intellij.testFramework.TestActionEvent
import com.intellij.testFramework.fixtures.BasePlatformTestCase

class KvistEditorActionsTest : BasePlatformTestCase() {
    private val actionIds = listOf(
        "com.kvist.intellij.RewriteAsSeniorAction",
        "com.kvist.intellij.TranslateSelectionAction",
        "com.kvist.intellij.ExplainAndFixAction",
    )

    private fun updated(actionId: String, withEditor: Boolean = true): com.intellij.openapi.actionSystem.Presentation {
        val action = ActionManager.getInstance().getAction(actionId)
        val context = SimpleDataContext.builder()
            .add(CommonDataKeys.PROJECT, project)
            .also { if (withEditor) it.add(CommonDataKeys.EDITOR, myFixture.editor) }
            .build()
        val event = TestActionEvent.createTestEvent(action, context)
        action.update(event)
        return event.presentation
    }

    fun testGroupIsInTheEditorContextMenuWithAllThreeActions() {
        val manager = ActionManager.getInstance()
        val group = manager.getAction("com.kvist.intellij.EditorPopupGroup") as DefaultActionGroup
        val popup = manager.getAction("EditorPopupMenu") as DefaultActionGroup

        assertTrue(group.isPopup)
        assertEquals("Kvist", group.templatePresentation.text)
        assertTrue(popup.getChildren(manager).any { it === group })
        assertEquals(actionIds, group.getChildren(null).map { manager.getId(it) })
    }

    fun testActionsReadEditorStateOnTheBackgroundThread() {
        for (id in actionIds) {
            val action: AnAction = ActionManager.getInstance().getAction(id)
            assertEquals(id, ActionUpdateThread.BGT, action.actionUpdateThread)
        }
    }

    fun testDisabledWithoutASelection() {
        myFixture.configureByText("Sample.txt", "some code here\n")

        for (id in actionIds) {
            val presentation = updated(id)
            assertTrue("$id should be visible", presentation.isVisible)
            assertFalse("$id should be disabled", presentation.isEnabled)
        }
    }

    fun testEnabledWithASelection() {
        myFixture.configureByText("Sample.txt", "some code here\n")
        myFixture.editor.selectionModel.setSelection(0, 4)

        for (id in actionIds) {
            val presentation = updated(id)
            assertTrue("$id should be visible", presentation.isVisible)
            assertTrue("$id should be enabled", presentation.isEnabled)
        }
    }

    fun testHiddenWithoutAnEditor() {
        for (id in actionIds) {
            val presentation = updated(id, withEditor = false)
            assertFalse("$id should be hidden", presentation.isVisible)
            assertFalse("$id should be disabled", presentation.isEnabled)
        }
    }
}
