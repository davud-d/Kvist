package com.devagent.intellij.runtime

import com.devagent.intellij.runtime.DevAgentBinaryResolver.SupportedOs
import com.devagent.intellij.ui.DevAgentNotifications
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.guessProjectDir
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.terminal.frontend.toolwindow.TerminalToolWindowTabsManager
import org.jetbrains.plugins.terminal.TerminalToolWindowFactory
import org.jetbrains.plugins.terminal.startup.TerminalProcessType
import java.io.IOException
import java.nio.file.Files
import java.nio.file.InvalidPathException
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString

object DevAgentLauncher {
    private val LOG = logger<DevAgentLauncher>()

    private data class LaunchSpec(
        val projectPath: Path,
        val binaryPath: Path,
        val operatingSystem: SupportedOs,
    ) {
        val command: List<String>
            get() = listOf(binaryPath.toString(), "--workspace", projectPath.toString())
    }

    fun launch(project: Project?) {
        if (project == null) {
            DevAgentNotifications.error(null, "Project is not open.")
            return
        }

        val launchSpec = prepareLaunch(project, notifyUser = true, logFailures = true) ?: return

        val terminalToolWindow = ToolWindowManager.getInstance(project)
            .getToolWindow(TerminalToolWindowFactory.TOOL_WINDOW_ID)

        if (terminalToolWindow == null) {
            LOG.warn("Terminal tool window is unavailable for project ${project.name}")
            DevAgentNotifications.error(project, "Failed to open terminal.")
            return
        }

        LOG.info(
            "Launching DevAgent in ${launchSpec.projectPath.invariantSeparatorsPathString} " +
                "with binary ${launchSpec.binaryPath.invariantSeparatorsPathString}"
        )

        try {
            TerminalToolWindowTabsManager.getInstance(project)
                .createTabBuilder()
                .workingDirectory(launchSpec.projectPath.toString())
                .processType(TerminalProcessType.NON_SHELL)
                .tabName("DevAgent")
                .requestFocus(true)
                .shellCommand(launchSpec.command)
                .createTab()
            LOG.info("DevAgent terminal tab created successfully for project ${project.name}")
        }
        catch (exception: IllegalStateException) {
            handleLaunchFailure(project, launchSpec, exception)
        }
        catch (exception: RuntimeException) {
            handleLaunchFailure(project, launchSpec, exception)
        }
    }

    fun isLaunchContextAvailable(project: Project?): Boolean {
        return project != null && prepareLaunch(project, notifyUser = false, logFailures = false) != null
    }

    private fun prepareLaunch(project: Project, notifyUser: Boolean, logFailures: Boolean): LaunchSpec? {
        val projectPath = resolveProjectPath(project, notifyUser, logFailures) ?: return null

        val operatingSystem = DevAgentBinaryResolver.currentOs()
            ?: return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = "This operating system is not supported.",
            )

        val expectedBinaryPath = DevAgentBinaryResolver.expectedRelativeBinaryPath(operatingSystem).invariantSeparatorsPathString
        val binaryPath = DevAgentBinaryResolver.resolveBinaryPath(operatingSystem, logDetails = logFailures)
            ?: return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = "DevAgent binary was not found at $expectedBinaryPath.",
            )

        val validationError = validateBinary(binaryPath, operatingSystem, logFailures)
        if (validationError != null) {
            return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = validationError,
            )
        }

        if (logFailures) {
            LOG.info(
                "Prepared DevAgent launch for project ${project.name}: " +
                    "projectPath=${projectPath.invariantSeparatorsPathString}, " +
                    "binaryPath=${binaryPath.invariantSeparatorsPathString}, os=$operatingSystem"
            )
        }

        return LaunchSpec(projectPath, binaryPath, operatingSystem)
    }

    private fun resolveProjectPath(project: Project, notifyUser: Boolean, logFailures: Boolean): Path? {
        val rawProjectPath = sequenceOf(project.guessProjectDir()?.path, project.basePath)
            .firstNotNullOfOrNull { it?.takeIf { candidate -> candidate.isNotBlank() } }
            ?: return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = "Project directory is unavailable.",
            )

        val projectPath = try {
            Path.of(rawProjectPath).normalize()
        }
        catch (exception: InvalidPathException) {
            return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = "Project directory is not a local filesystem path.",
                exception = exception,
            )
        }

        if (!Files.isDirectory(projectPath)) {
            return fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                message = "Project directory does not exist: ${projectPath.invariantSeparatorsPathString}.",
            )
        }

        if (logFailures) {
            LOG.info("Resolved project directory: ${projectPath.invariantSeparatorsPathString}")
        }

        return projectPath
    }

    private fun validateBinary(binaryPath: Path, operatingSystem: SupportedOs, logFailures: Boolean): String? {
        if (!Files.isReadable(binaryPath)) {
            return "DevAgent binary is not readable: ${binaryPath.invariantSeparatorsPathString}."
        }

        val binarySize = try {
            Files.size(binaryPath)
        }
        catch (exception: IOException) {
            if (logFailures) {
                LOG.warn("Failed to inspect DevAgent binary ${binaryPath.invariantSeparatorsPathString}", exception)
            }
            return "Failed to inspect DevAgent binary: ${exception.userFacingMessage()}."
        }
        catch (exception: SecurityException) {
            if (logFailures) {
                LOG.warn("Failed to inspect DevAgent binary ${binaryPath.invariantSeparatorsPathString}", exception)
            }
            return "Failed to inspect DevAgent binary: ${exception.userFacingMessage()}."
        }

        if (binarySize <= 0L) {
            return "DevAgent binary is empty: ${binaryPath.invariantSeparatorsPathString}."
        }

        if (operatingSystem == SupportedOs.LINUX && !Files.isExecutable(binaryPath)) {
            return "DevAgent binary is not executable. Rebuild the plugin so ${DevAgentBinaryResolver.expectedRelativeBinaryPath(operatingSystem).invariantSeparatorsPathString} is packaged with execute permissions."
        }

        return null
    }

    private fun handleLaunchFailure(project: Project, launchSpec: LaunchSpec, exception: RuntimeException) {
        LOG.warn(
            "Failed to create DevAgent terminal tab for ${project.name}. " +
                "command=${launchSpec.command.joinToString(" ")}",
            exception,
        )
        DevAgentNotifications.error(project, "Failed to start DevAgent: ${exception.userFacingMessage()}.")
    }

    private fun fail(
        project: Project,
        notifyUser: Boolean,
        logFailures: Boolean,
        message: String,
        exception: Throwable? = null,
    ): LaunchSpec? {
        if (logFailures) {
            if (exception != null) {
                LOG.warn(message, exception)
            }
            else {
                LOG.warn(message)
            }
        }

        if (notifyUser) {
            DevAgentNotifications.error(project, message)
        }

        return null
    }

    private fun Throwable.userFacingMessage(): String {
        val detail = message?.lineSequence()?.firstOrNull()?.trim().orEmpty().trimEnd('.')
        return detail.ifBlank { javaClass.simpleName }
    }
}