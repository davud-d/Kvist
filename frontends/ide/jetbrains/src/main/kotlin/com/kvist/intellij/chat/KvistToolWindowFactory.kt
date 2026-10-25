package com.kvist.intellij.chat

import com.intellij.icons.AllIcons
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.text.StringUtil
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBLabel
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefApp
import com.intellij.util.ui.JBUI
import com.kvist.intellij.runtime.KvistLauncher
import java.awt.BorderLayout
import javax.swing.Icon
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JPanel

private const val NO_JCEF_MESSAGE =
    "The Kvist chat needs the IDE's embedded browser (JCEF), and this IDE runs without it. " +
        "Pick a boot runtime with JCEF (Help | Find Action | Choose Boot Java Runtime for the IDE) and restart, " +
        "or use Kvist in the terminal."

private const val MISSING_JCEF_MESSAGE =
    "The Kvist chat needs the IDE's embedded browser (JCEF). " +
        "Enable Web Browser (JCEF) in Settings | Plugins and restart, or use Kvist in the terminal."

class KvistToolWindowFactory : ToolWindowFactory, DumbAware {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = try {
            if (!JBCefApp.isSupported()) {
                LOG.warn("JCEF is not supported in this IDE; the Kvist tool window offers the terminal instead")
                show(toolWindow, terminalFallback(project, NO_JCEF_MESSAGE))
                return
            }
            KvistChatPanel(project)
        }
        catch (error: NoClassDefFoundError) {
            // An optional JCEF plugin may be absent or disabled on 2026.2+.
            LOG.warn("JCEF classes are unavailable; the Kvist tool window offers the terminal instead", error)
            show(toolWindow, terminalFallback(project, MISSING_JCEF_MESSAGE))
            return
        }
        catch (exception: IllegalStateException) {
            LOG.warn("Failed to open the Kvist chat", exception)
            show(toolWindow, terminalFallback(project, "The Kvist chat could not start: ${exception.message}"))
            return
        }
        val content = ContentFactory.getInstance().createContent(panel.component, "", false)
        content.setDisposer(panel)
        toolWindow.contentManager.addContent(content)
        toolWindow.setTitleActions(
            listOfNotNull(
                PanelAction("New Session", AllIcons.General.Add) { panel.host.newSession() },
                PanelAction("Settings", AllIcons.General.Settings) { panel.host.showSettings() },
                PanelAction("Reload", AllIcons.Actions.Refresh) { panel.load() },
                PanelAction("Open Kvist in Terminal", AllIcons.Actions.Execute) { KvistLauncher.launch(project) },
            )
        )
    }

    private fun show(toolWindow: ToolWindow, component: JComponent) {
        toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(component, "", false))
    }

    // The old launcher stays reachable: the same terminal mode the plugin always had.
    private fun terminalFallback(project: Project, reason: String): JComponent {
        val button = JButton("Open Kvist in Terminal")
        button.addActionListener { KvistLauncher.launch(project) }
        val buttons = JPanel()
        buttons.add(button)
        val panel = JPanel(BorderLayout(0, JBUI.scale(8)))
        panel.border = JBUI.Borders.empty(12)
        panel.add(JBLabel("<html>${StringUtil.escapeXmlEntities(reason)}</html>"), BorderLayout.NORTH)
        panel.add(buttons, BorderLayout.CENTER)
        return panel
    }

    private class PanelAction(text: String, icon: Icon, private val run: () -> Unit) : DumbAwareAction(text, null, icon) {
        override fun actionPerformed(event: AnActionEvent) = run()
    }

    private companion object {
        val LOG = logger<KvistToolWindowFactory>()
    }
}
