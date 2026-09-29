package com.kvist.intellij.runtime

import com.intellij.ide.plugins.PluginManager
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.util.SystemInfo
import java.nio.file.Files
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString

object KvistBinaryResolver {
    private val LOG = logger<KvistBinaryResolver>()
    private const val PLUGIN_ID = "com.kvist.intellij"
    private val WINDOWS_BINARY: Path = Path.of("bin", "win", "kvist.exe")
    private val LINUX_BINARY: Path = Path.of("bin", "linux", "kvist.bin")

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
        val pluginPath = resolveInstalledPluginPath(logDetails) ?: return null
        val relativePath = expectedRelativeBinaryPath(os)

        val binaryPath = pluginPath.resolve(relativePath).normalize()
        if (logDetails) {
            LOG.info("Resolved Kvist binary candidate for $os: ${binaryPath.invariantSeparatorsPathString}")
        }

        return binaryPath.takeIf { Files.isRegularFile(it) }.also {
            if (it == null && logDetails) {
                LOG.warn("Kvist binary is missing: ${binaryPath.invariantSeparatorsPathString}")
            }
        }
    }

    private fun resolveInstalledPluginPath(logDetails: Boolean): Path? {
        val descriptor = PluginManager.getPluginByClass(KvistBinaryResolver::class.java)
            ?: PluginManagerCore.getPlugin(PluginId.getId(PLUGIN_ID))

        val pluginPath = descriptor?.pluginPath?.normalize()?.takeIf { Files.isDirectory(it) }
        if (logDetails) {
            if (pluginPath != null) {
                LOG.info("Resolved installed plugin path: ${pluginPath.invariantSeparatorsPathString}")
            }
            else {
                LOG.warn("Failed to resolve installed plugin path for $PLUGIN_ID")
            }
        }

        return pluginPath
    }
}