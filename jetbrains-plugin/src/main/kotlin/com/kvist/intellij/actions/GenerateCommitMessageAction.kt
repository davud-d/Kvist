package com.kvist.intellij.actions

import com.kvist.intellij.commit.CommitChangesCollector
import com.kvist.intellij.commit.CommitMessageException
import com.kvist.intellij.commit.KvistCommitMessageClient
import com.kvist.intellij.runtime.KvistLauncher
import com.kvist.intellij.ui.KvistNotifications
import com.intellij.icons.AllIcons
import com.intellij.ide.ActivityTracker
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ModalityState
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.intellij.openapi.progress.impl.BackgroundableProcessIndicator
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.vcs.CommitMessageI
import com.intellij.openapi.vcs.FilePath
import com.intellij.openapi.vcs.VcsDataKeys
import com.intellij.openapi.vcs.changes.Change
import com.intellij.ui.AnimatedIcon

class GenerateCommitMessageAction : DumbAwareAction() {
    // The commit UI is Swing state; reading it belongs on the EDT.
    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.EDT

    override fun update(event: AnActionEvent) {
        val presentation = event.presentation
        val project = event.project
        presentation.isEnabledAndVisible = project != null &&
            event.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) != null &&
            event.getData(VcsDataKeys.COMMIT_MESSAGE_CONTROL) != null
        if (project == null) {
            return
        }

        // Without a visible running state the first click looked ignored and
        // users clicked again, starting a second generation.
        if (project.service<GenerationState>().isRunning) {
            presentation.icon = AnimatedIcon.Default.INSTANCE
            presentation.hoveredIcon = AllIcons.Actions.Suspend
            presentation.text = "Stop Generating Commit Message"
            return
        }
        presentation.icon = templatePresentation.icon
        presentation.hoveredIcon = null
        presentation.text = templatePresentation.text
    }

    override fun actionPerformed(event: AnActionEvent) {
        val project = event.project ?: return
        val state = project.service<GenerationState>()
        if (state.isRunning) {
            state.cancel()
            return
        }

        val workflowUi = event.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) ?: return
        val messageControl = event.getData(VcsDataKeys.COMMIT_MESSAGE_CONTROL) ?: return
        val changes = workflowUi.getIncludedChanges()
        val unversioned = workflowUi.getIncludedUnversionedFiles()
        if (changes.isEmpty() && unversioned.isEmpty()) {
            KvistNotifications.error(project, "Select the changes to commit first.")
            return
        }

        val task = GenerateTask(project, changes, unversioned, messageControl, ModalityState.current())
        // An indicator created here, rather than by queue(), exists before the
        // task starts, so a Stop click can never fall into a gap.
        val indicator = BackgroundableProcessIndicator(task)
        state.start(indicator)
        ProgressManager.getInstance().runProcessWithProgressAsynchronously(task, indicator)
    }

    @Service(Service.Level.PROJECT)
    class GenerationState {
        // Written and read on the EDT only.
        private var indicator: ProgressIndicator? = null

        val isRunning: Boolean
            get() = indicator != null

        fun start(newIndicator: ProgressIndicator) {
            indicator = newIndicator
            refreshToolbars()
        }

        fun cancel() {
            indicator?.cancel()
        }

        fun finish() {
            indicator = null
            refreshToolbars()
        }

        // Toolbars otherwise pick up the new state only on their next idle
        // update, which leaves the spinner visibly late.
        private fun refreshToolbars() {
            ActivityTracker.getInstance().inc()
        }
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
            try {
                generate(indicator)
            }
            finally {
                // Not onFinished(): under a modal commit dialog that callback
                // waits for the dialog to close, and the spinner would stick.
                ApplicationManager.getApplication().invokeLater(
                    { project.service<GenerationState>().finish() },
                    modalityState,
                    project.disposed,
                )
            }
        }

        private fun generate(indicator: ProgressIndicator) {
            val binaryPath = KvistLauncher.prepareBinary(project) ?: return
            indicator.text = "Collecting changes"
            val payload = CommitChangesCollector.buildPayload(project, changes, unversioned)
            indicator.checkCanceled()
            indicator.text = "Waiting for the model"
            val message = try {
                KvistCommitMessageClient.generate(binaryPath, payload, indicator)
            }
            catch (exception: CommitMessageException) {
                KvistNotifications.error(project, exception.message ?: "Failed to generate a commit message.")
                return
            }
            indicator.checkCanceled()
            ApplicationManager.getApplication().invokeLater(
                { messageControl.setCommitMessage(message) },
                modalityState,
                project.disposed,
            )
        }
    }
}
