package com.devagent.intellij.runtime

import com.intellij.ide.plugins.PluginManager
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.util.SystemInfo
import java.nio.file.Files
import java.nio.file.Path

object DevAgentBinaryResolver {
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

    fun resolveBinaryPath(os: SupportedOs): Path? {
        val pluginPath = resolvePluginPath() ?: return null
        val relativePath = when (os) {
            SupportedOs.WINDOWS -> WINDOWS_BINARY
            SupportedOs.LINUX -> LINUX_BINARY
        }

        val binaryPath = pluginPath.resolve(relativePath).normalize()
        return binaryPath.takeIf { Files.isRegularFile(it) }
    }

    private fun resolvePluginPath(): Path? {
        val descriptor = PluginManager.getPluginByClass(DevAgentBinaryResolver::class.java)
            ?: PluginManagerCore.getPlugin(PluginId.getId(PLUGIN_ID))

        return descriptor?.pluginPath
    }
}