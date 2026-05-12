package com.devagent.intellij.runtime

import com.devagent.intellij.runtime.DevAgentBinaryResolver.SupportedOs
import com.devagent.intellij.ui.DevAgentNotifications
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.project.guessProjectDir
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.terminal.frontend.toolwindow.TerminalToolWindowTabsManager
import org.jetbrains.plugins.terminal.TerminalToolWindowFactory
import java.io.ByteArrayOutputStream
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
        val binaryCommand: List<String>
            get() = listOf(binaryPath.toString(), "--workspace", projectPath.toString())

        val command: List<String>
            get() = if (operatingSystem == SupportedOs.LINUX) {
                linuxShellCommand(binaryCommand)
            }
            else {
                binaryCommand
            }
    }

    fun launch(project: Project?) {
        if (project == null) {
            DevAgentNotifications.error(null, "Project is not open.")
            return
        }

        val launchSpec = prepareLaunch(project, notifyUser = true, logFailures = true) ?: return

        if (!ensureBinaryReadyForLaunch(project, launchSpec)) {
            return
        }

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
            ?: run {
                fail(
                    project = project,
                    notifyUser = notifyUser,
                    logFailures = logFailures,
                    userMessage = "This operating system is not supported.",
                )
                return null
            }

        val expectedBinaryPath = DevAgentBinaryResolver.expectedRelativeBinaryPath(operatingSystem).invariantSeparatorsPathString
        val binaryPath = DevAgentBinaryResolver.resolveBinaryPath(operatingSystem, logDetails = logFailures)
            ?: run {
                fail(
                    project = project,
                    notifyUser = notifyUser,
                    logFailures = logFailures,
                    userMessage = "DevAgent binary was not found.",
                    logMessage = "DevAgent binary was not found at $expectedBinaryPath.",
                )
                return null
            }

        val validationError = validateBinary(binaryPath, logFailures)
        if (validationError != null) {
            fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                userMessage = "Failed to start DevAgent.",
                logMessage = validationError,
            )
            return null
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
            ?: run {
                fail(
                    project = project,
                    notifyUser = notifyUser,
                    logFailures = logFailures,
                    userMessage = "Project path is unavailable.",
                    logMessage = "Project path is unavailable: basePath and guessProjectDir are empty.",
                )
                return null
            }

        val projectPath = try {
            Path.of(rawProjectPath).normalize()
        }
        catch (exception: InvalidPathException) {
            fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                userMessage = "Project path is unavailable.",
                logMessage = "Project path is unavailable: $rawProjectPath",
                exception = exception,
            )
            return null
        }

        if (!Files.isDirectory(projectPath)) {
            fail(
                project = project,
                notifyUser = notifyUser,
                logFailures = logFailures,
                userMessage = "Project path is unavailable.",
                logMessage = "Project path is unavailable: ${projectPath.invariantSeparatorsPathString} is not a directory.",
            )
            return null
        }

        if (logFailures) {
            LOG.info("Resolved project directory: ${projectPath.invariantSeparatorsPathString}")
        }

        return projectPath
    }

    private fun validateBinary(binaryPath: Path, logFailures: Boolean): String? {
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

        return null
    }

    private fun ensureBinaryReadyForLaunch(project: Project, launchSpec: LaunchSpec): Boolean {
        if (launchSpec.operatingSystem != SupportedOs.LINUX || Files.isExecutable(launchSpec.binaryPath)) {
            return true
        }

        val chmodResult = try {
            runLinuxChmod(launchSpec.binaryPath)
        }
        catch (exception: IOException) {
            fail(
                project = project,
                notifyUser = true,
                logFailures = true,
                userMessage = "Failed to start DevAgent.",
                logMessage = "Failed to update execute permissions for ${launchSpec.binaryPath.invariantSeparatorsPathString}",
                exception = exception,
            )
            null
        }
        catch (exception: InterruptedException) {
            Thread.currentThread().interrupt()
            fail(
                project = project,
                notifyUser = true,
                logFailures = true,
                userMessage = "Failed to start DevAgent.",
                logMessage = "Interrupted while updating execute permissions for ${launchSpec.binaryPath.invariantSeparatorsPathString}",
                exception = exception,
            )
            null
        }

        if (chmodResult == null) {
            return false
        }

        if (chmodResult.exitCode != 0 || !Files.isExecutable(launchSpec.binaryPath)) {
            val suffix = chmodResult.output
                .takeIf { it.isNotBlank() }
                ?.let { ": $it" }
                .orEmpty()
            fail(
                project = project,
                notifyUser = true,
                logFailures = true,
                userMessage = "Failed to start DevAgent.",
                logMessage = "Failed to make DevAgent binary executable with chmod +x: ${launchSpec.binaryPath.invariantSeparatorsPathString}$suffix",
            )
            return false
        }

        LOG.info("Updated execute permissions for ${launchSpec.binaryPath.invariantSeparatorsPathString} using chmod +x")
        return true
    }

    private fun linuxShellCommand(binaryCommand: List<String>): List<String> {
        // DevAgent renders a TUI inside the IDE terminal, so Linux launches must avoid
        // interactive shell startup files that can dump banners, prompts, or auto-started
        // tools into stdout before the UI is ready.
        val shellPath = System.getenv("SHELL")?.trim().orEmpty().ifBlank { null } ?: return binaryCommand
        val commandString = binaryCommand.joinToString(" ") { quoteForShell(it) }
        val shellName = try {
            Path.of(shellPath).fileName?.toString()
        }
        catch (_: InvalidPathException) {
            null
        }

        return when (shellName) {
            "fish" -> listOf(shellPath, "--no-config", "-c", commandString)
            "bash" -> listOf(shellPath, "--noprofile", "--norc", "-c", commandString)
            "zsh" -> listOf(shellPath, "-f", "-c", commandString)
            else -> binaryCommand
        }
    }

    private fun quoteForShell(argument: String): String {
        val escaped = buildString(argument.length + 8) {
            for (character in argument) {
                when (character) {
                    '\\' -> append("\\\\")
                    '"' -> append("\\\"")
                    '$' -> append("\\$")
                    '`' -> append("\\`")
                    else -> append(character)
                }
            }
        }
        return "\"$escaped\""
    }

    private fun runLinuxChmod(binaryPath: Path): ChmodResult {
        val process = ProcessBuilder("chmod", "+x", binaryPath.toString())
            .redirectErrorStream(true)
            .start()

        val output = ByteArrayOutputStream().use { buffer ->
            process.inputStream.use { input ->
                input.copyTo(buffer)
            }
            buffer.toString(Charsets.UTF_8).trim()
        }

        return ChmodResult(
            exitCode = process.waitFor(),
            output = output,
        )
    }

    private data class ChmodResult(
        val exitCode: Int,
        val output: String,
    )

    private fun handleLaunchFailure(project: Project, launchSpec: LaunchSpec, exception: RuntimeException) {
        LOG.warn(
            "Failed to create DevAgent terminal tab for ${project.name}. " +
                "command=${launchSpec.command.joinToString(" ")}",
            exception,
        )
        DevAgentNotifications.error(project, "Failed to start DevAgent.")
    }

    private fun fail(
        project: Project,
        notifyUser: Boolean,
        logFailures: Boolean,
        userMessage: String,
        logMessage: String = userMessage,
        exception: Throwable? = null,
    ) {
        if (logFailures) {
            if (exception != null) {
                LOG.warn(logMessage, exception)
            }
            else {
                LOG.warn(logMessage)
            }
        }

        if (notifyUser) {
            DevAgentNotifications.error(project, userMessage)
        }
    }

    private fun Throwable.userFacingMessage(): String {
        val detail = message?.lineSequence()?.firstOrNull()?.trim().orEmpty().trimEnd('.')
        return detail.ifBlank { javaClass.simpleName }
    }
}