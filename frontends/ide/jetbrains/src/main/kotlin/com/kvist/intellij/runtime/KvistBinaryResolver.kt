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
    private const val PLUGIN_ID = "com.kvist.agent"
    private val WINDOWS_BINARY: Path = Path.of("bin", "win", "kvist.exe")
    private val LINUX_BINARY: Path = Path.of("bin", "linux", "kvist.bin")
    private val MACOS_ARM64_BINARY: Path = Path.of("bin", "mac-arm64", "kvist.bin")
    private val MACOS_X64_BINARY: Path = Path.of("bin", "mac-x64", "kvist.bin")

    enum class SupportedOs {
        WINDOWS,
        LINUX,
        MACOS_ARM64,
        MACOS_X64,
        ;

        /** Whether the binary is started through a POSIX shell and needs the execute bit. */
        val isPosix: Boolean
            get() = this != WINDOWS
    }

    fun currentOs(): SupportedOs? {
        return when {
            SystemInfo.isWindows -> SupportedOs.WINDOWS
            SystemInfo.isLinux -> SupportedOs.LINUX
            // An IDE running under Rosetta reports x64 and takes the x64 binary, which Rosetta runs too.
            SystemInfo.isMac -> if (SystemInfo.isAarch64) SupportedOs.MACOS_ARM64 else SupportedOs.MACOS_X64
            else -> null
        }
    }

    fun expectedRelativeBinaryPath(os: SupportedOs): Path {
        return when (os) {
            SupportedOs.WINDOWS -> WINDOWS_BINARY
            SupportedOs.LINUX -> LINUX_BINARY
            SupportedOs.MACOS_ARM64 -> MACOS_ARM64_BINARY
            SupportedOs.MACOS_X64 -> MACOS_X64_BINARY
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