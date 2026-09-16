package com.kvist.intellij.actions

import com.kvist.intellij.commit.CommitChangesCollector
import com.kvist.intellij.commit.CommitMessageException
import com.kvist.intellij.commit.KvistCommitMessageClient
import com.kvist.intellij.runtime.KvistLauncher
import com.kvist.intellij.ui.KvistNotifications
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.Task
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.vcs.CommitMessageI
import com.intellij.openapi.vcs.FilePath
import com.intellij.openapi.vcs.VcsDataKeys
import com.intellij.openapi.vcs.changes.Change

class GenerateCommitMessageAction : DumbAwareAction() {
    // The commit UI is Swing state; reading it belongs on the EDT.
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.EDT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabledAndVisible = event.project != null &&
            event.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) != null &&
            event.getData(VcsDataKeys.COMMIT_MESSAGE_CONTROL) != null
    }

    override fun actionPerformed(event: AnActionEvent) {
        val project = event.project ?: return
        val workflowUi = event.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) ?: return
        val messageControl = event.getData(VcsDataKeys.COMMIT_MESSAGE_CONTROL) ?: return

        val changes = workflowUi.getIncludedChanges()
        val unversioned = workflowUi.getIncludedUnversionedFiles()
        if (changes.isEmpty() && unversioned.isEmpty()) {
            KvistNotifications.error(project, "Select the changes to commit first.")
            return
        }

        GenerateTask(project, changes, unversioned, messageControl, ModalityState.current()).queue()
    }

    private class GenerateTask(
        project: Project,
        private val changes: List<Change>,
        private val unversioned: List<FilePath>,
        private val messageControl: CommitMessageI,
        // Captured from the commit dialog: a non-modal state would hold the
        // result back until a modal commit dialog closes.
        private val modalityState: ModalityState,
    ) : Task.Backgroundable(project, "Generating commit message with Kvist", true) {

        override fun run(indicator: ProgressIndicator) {
            val binaryPath = KvistLauncher.prepareBinary(project) ?: return
            indicator.text = "Collecting changes"
            val payload = CommitChangesCollector.buildPayload(project, changes, unversioned)
            indicator.text = "Waiting for the model"
            val message = try {
                KvistCommitMessageClient.generate(binaryPath, payload, indicator)
            }
            catch (exception: CommitMessageException) {
                KvistNotifications.error(project, exception.message ?: "Failed to generate a commit message.")
                return
            }
            ApplicationManager.getApplication().invokeLater(
                { messageControl.setCommitMessage(message) },
                modalityState,
                project.disposed,
            )
        }
    }
}
