package com.kvist.intellij.completion

import com.intellij.openapi.vfs.VirtualFile
import java.nio.file.Path

/** Check names only: completion must never inspect the contents of environment files. */
internal object CompletionFilePolicy {
    fun isBlocked(path: String): Boolean =
        path.split('/', '\\').any { it.startsWith(".env", ignoreCase = true) }

    fun isBlocked(file: VirtualFile): Boolean =
        isBlocked(file.path) || file.canonicalPath?.let(::isBlocked) == true

    fun isBlocked(workspace: Path, filePath: String): Boolean {
        if (isBlocked(filePath) || isBlocked(workspace.toString())) return true
        val path = try {
            workspace.resolve(filePath)
        }
        catch (_: java.nio.file.InvalidPathException) {
            return true
        }
        // Resolving a symlink reads filesystem metadata, never file contents. Unsaved or
        // non-local files may have no real path, so retain the lexical check in that case.
        val canonical = try {
            path.toRealPath().toString()
        }
        catch (_: java.io.IOException) {
            return false
        }
        catch (_: SecurityException) {
            return true
        }
        return isBlocked(canonical)
    }
}
