package com.kvist.intellij.selection

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.project.Project
import com.intellij.platform.ide.progress.withBackgroundProgress
import com.kvist.intellij.selection.llm.KvistLlmClient
import com.kvist.intellij.selection.llm.KvistLlmException
import com.kvist.intellij.selection.llm.KvistLlmRequest
import com.kvist.intellij.selection.llm.KvistProcessLlmClient
import com.kvist.intellij.ui.KvistNotifications
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Sends a selection request to the model in a cancellable background progress and hands the
 * reply back on the EDT. The scope belongs to the project, so closing it cancels what is in flight.
 */
@Service(Service.Level.PROJECT)
class KvistSelectionTaskRunner(
    private val project: Project,
    private val scope: CoroutineScope,
) {
    private val client: KvistLlmClient = KvistProcessLlmClient(project)

    /** [onReply] runs on the EDT with a non-blank reply. Failures are reported here. */
    fun run(progressTitle: String, request: KvistLlmRequest, onReply: (String) -> Unit) {
        scope.launch {
            val reply = try {
                withBackgroundProgress(project, progressTitle, cancellable = true) {
                    client.complete(request).text
                }
            }
            catch (exception: CancellationException) {
                throw exception
            }
            catch (exception: KvistLlmException) {
                LOG.warn("Kvist selection request failed", exception)
                KvistNotifications.error(project, exception.message ?: "Request failed.")
                return@launch
            }

            if (reply.isBlank()) {
                KvistNotifications.error(project, "Empty model response.")
                return@launch
            }
            ApplicationManager.getApplication().invokeLater({ onReply(reply) }, project.disposed)
        }
    }

    companion object {
        private val LOG = logger<KvistSelectionTaskRunner>()
    }
}
