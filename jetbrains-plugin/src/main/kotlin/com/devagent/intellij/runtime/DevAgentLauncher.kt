package com.devagent.intellij.runtime

import com.devagent.intellij.runtime.DevAgentBinaryResolver.SupportedOs
import com.devagent.intellij.ui.DevAgentNotifications
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.terminal.frontend.toolwindow.TerminalToolWindowTabsManager
import org.jetbrains.plugins.terminal.TerminalToolWindowFactory
import org.jetbrains.plugins.terminal.startup.TerminalProcessType
import java.nio.file.Files
import java.nio.file.Path

object DevAgentLauncher {
    fun launch(project: Project?) {
        if (project == null) {
            DevAgentNotifications.error(null, "Project is not open.")
            return
        }

        val projectPath = project.basePath?.takeIf { it.isNotBlank() }
        if (projectPath == null) {
            DevAgentNotifications.error(project, "Project path is unavailable.")
            return
        }

        val operatingSystem = DevAgentBinaryResolver.currentOs()
        if (operatingSystem == null) {
            DevAgentNotifications.error(project, "This operating system is not supported.")
            return
        }

        val binaryPath = DevAgentBinaryResolver.resolveBinaryPath(operatingSystem)
        if (binaryPath == null) {
            DevAgentNotifications.error(project, "DevAgent binary was not found.")
            return
        }

        if (operatingSystem == SupportedOs.LINUX && !ensureExecutable(binaryPath)) {
            DevAgentNotifications.error(project, "Failed to make DevAgent binary executable.")
            return
        }

        val terminalToolWindow = ToolWindowManager.getInstance(project)
            .getToolWindow(TerminalToolWindowFactory.TOOL_WINDOW_ID)

        if (terminalToolWindow == null) {
            DevAgentNotifications.error(project, "Failed to open terminal.")
            return
        }

        try {
            TerminalToolWindowTabsManager.getInstance(project)
                .createTabBuilder()
                .workingDirectory(projectPath)
                .processType(TerminalProcessType.NON_SHELL)
                .tabName("DevAgent")
                .requestFocus(true)
                .shellCommand(listOf(binaryPath.toString(), "--workspace", projectPath))
                .createTab()
        }
        catch (_: Exception) {
            DevAgentNotifications.error(project, "Failed to start DevAgent.")
        }
    }

    private fun ensureExecutable(binaryPath: Path): Boolean {
        if (Files.isExecutable(binaryPath)) {
            return true
        }

        return runCatching {
            binaryPath.toFile().setExecutable(true, false) && Files.isExecutable(binaryPath)
        }.getOrDefault(false)
    }
}