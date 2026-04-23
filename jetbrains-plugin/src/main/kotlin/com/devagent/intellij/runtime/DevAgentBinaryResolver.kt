package com.devagent.intellij.runtime

import com.intellij.ide.plugins.PluginManager
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.util.SystemInfo
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString

object DevAgentBinaryResolver {
    private val LOG = logger<DevAgentBinaryResolver>()
    private const val PLUGIN_ID = "com.devagent.intellij"
    private val WINDOWS_BINARY: Path = Path.of("bin", "win", "devagent.exe")
    private val LINUX_BINARY: Path = Path.of("bin", "linux", "devagent.bin")

    enum class SupportedOs {
        WINDOWS,
        LINUX,
    }

    fun currentOs(): SupportedOs? {
        return when {
            SystemInfo.isWindows -> SupportedOs.WINDOWS
            SystemInfo.isLinux -> SupportedOs.LINUX
            else -> null
        }
    }

    fun expectedRelativeBinaryPath(os: SupportedOs): Path {
        return when (os) {
            SupportedOs.WINDOWS -> WINDOWS_BINARY
            SupportedOs.LINUX -> LINUX_BINARY
        }
    }

    fun resolveBinaryPath(os: SupportedOs, logDetails: Boolean = true): Path? {
        val pluginPath = resolvePluginPath(logDetails) ?: return null
        val relativePath = expectedRelativeBinaryPath(os)

        val binaryPath = pluginPath.resolve(relativePath).normalize()
        if (logDetails) {
            LOG.info("Resolved DevAgent binary candidate for $os: ${binaryPath.invariantSeparatorsPathString}")
        }

        return binaryPath.takeIf { Files.isRegularFile(it) }.also {
            if (it == null && logDetails) {
                LOG.warn("DevAgent binary is missing: ${binaryPath.invariantSeparatorsPathString}")
            }
        }
    }

    private fun resolvePluginPath(logDetails: Boolean): Path? {
        val descriptor = PluginManager.getPluginByClass(DevAgentBinaryResolver::class.java)
            ?: PluginManagerCore.getPlugin(PluginId.getId(PLUGIN_ID))

        val pluginPath = descriptor?.pluginPath
        if (logDetails) {
            if (pluginPath != null) {
                LOG.info("Resolved plugin installation path: ${pluginPath.invariantSeparatorsPathString}")
            }
            else {
                LOG.warn("Failed to resolve plugin installation path for $PLUGIN_ID")
            }
        }

        return pluginPath
    }
}