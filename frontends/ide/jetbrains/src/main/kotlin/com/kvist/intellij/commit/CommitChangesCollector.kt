package com.kvist.intellij.commit

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.diff.impl.patch.IdeaTextPatchBuilder
import com.intellij.openapi.diff.impl.patch.UnifiedDiffWriter
import com.intellij.openapi.project.Project
import com.intellij.openapi.vcs.FilePath
import com.intellij.openapi.vcs.ProjectLevelVcsManager
import com.intellij.openapi.vcs.VcsException
import com.intellij.openapi.vcs.changes.Change
import com.intellij.openapi.vcs.changes.ChangesUtil
import com.intellij.openapi.vcs.changes.CurrentContentRevision
import com.intellij.openapi.vfs.VirtualFile
import git4idea.commands.Git
import git4idea.commands.GitCommand
import git4idea.commands.GitLineHandler
import java.io.IOException
import java.io.StringWriter
import java.nio.file.InvalidPathException
import java.nio.file.Path
import kotlin.io.path.invariantSeparatorsPathString

/**
 * Builds the JSON payload `kvist --commit-message` reads on stdin.
 *
 * Secret files are deliberately not filtered here: the core owns that list,
 * and a second copy in the plugin would drift from it.
 */
object CommitChangesCollector {
    private val LOG = logger<CommitChangesCollector>()

    // The core keeps ~30k tokens of diff; this only stops a generated or
    // minified file from turning into megabytes on the process pipe.
    private const val MAX_PATCH_CHARS = 200_000
    private const val RECENT_SUBJECT_COUNT = 15

    fun buildPayload(project: Project, changes: List<Change>, unversioned: List<FilePath>): String {
        val allChanges = changes + unversioned.map { Change(null, CurrentContentRevision.create(it)) }
        val vcsManager = ProjectLevelVcsManager.getInstance(project)
        val changesByRoot = allChanges.groupBy { vcsManager.getVcsRootFor(ChangesUtil.getFilePath(it)) }

        val files = JsonArray()
        for ((root, rootChanges) in changesByRoot) {
            if (root == null) {
                continue
            }
            rootChanges.mapNotNull { patchEntry(project, root, it) }.forEach(files::add)
        }

        val firstRoot = changesByRoot.keys.firstOrNull { it != null }
        val subjects = JsonArray()
        firstRoot?.let { recentSubjects(project, it) }.orEmpty().forEach(subjects::add)

        val payload = JsonObject()
        payload.add("files", files)
        payload.add("recent_subjects", subjects)
        return payload.toString()
    }

    private fun patchEntry(project: Project, root: VirtualFile, change: Change): JsonObject? {
        val rootPath = root.toNioPath()
        val filePath = ChangesUtil.getFilePath(change)
        val patchText = try {
            // honorExcludedFromCommit=true keeps partially staged files to the
            // lines actually ticked in the commit dialog.
            val patches = IdeaTextPatchBuilder.buildPatch(project, listOf(change), rootPath, false, true)
            val writer = StringWriter()
            UnifiedDiffWriter.write(project, patches, writer, "\n", null)
            writer.toString()
        }
        catch (exception: VcsException) {
            LOG.warn("Failed to build a patch for ${filePath.path}", exception)
            return null
        }
        catch (exception: IOException) {
            LOG.warn("Failed to write a patch for ${filePath.path}", exception)
            return null
        }

        val relativePath = try {
            rootPath.relativize(Path.of(filePath.path)).invariantSeparatorsPathString
        }
        catch (exception: InvalidPathException) {
            LOG.warn("Skipping a change with an unusable path: ${filePath.path}", exception)
            return null
        }
        catch (exception: IllegalArgumentException) {
            LOG.warn("Skipping a change outside its VCS root: ${filePath.path}", exception)
            return null
        }

        val entry = JsonObject()
        entry.addProperty("path", relativePath)
        entry.addProperty("patch", patchText.take(MAX_PATCH_CHARS))
        return entry
    }

    private fun recentSubjects(project: Project, root: VirtualFile): List<String> {
        val handler = GitLineHandler(project, root, GitCommand.LOG)
        handler.addParameters("--no-merges", "-n", RECENT_SUBJECT_COUNT.toString(), "--format=%s")
        handler.setSilent(true)
        val result = Git.getInstance().runCommand(handler)
        // A root that is not Git, or has no commits yet, simply has no history
        // to imitate; the prompt handles an empty list.
        if (!result.success()) {
            LOG.info("No commit history for ${root.path}: ${result.errorOutputAsJoinedString}")
            return emptyList()
        }
        return result.output.filter { it.isNotBlank() }
    }
}
