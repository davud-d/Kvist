package com.kvist.intellij.editor

import com.intellij.openapi.fileEditor.impl.EditorTabTitleProvider
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.text.StringUtil
import com.intellij.openapi.vfs.VirtualFile

private const val TITLE_LIMIT = 40

/** "Kvist | <session name>", as the terminals title themselves; a session without a name keeps plain "Kvist". */
class KvistPageTabTitleProvider : EditorTabTitleProvider, DumbAware {
    override fun getEditorTabTitle(project: Project, file: VirtualFile): String? {
        val title = (file as? KvistPageFile)?.sessionTitle?.trim().orEmpty()
        if (title.isEmpty()) return null
        return "Kvist | " + StringUtil.shortenTextWithEllipsis(title, TITLE_LIMIT, 0)
    }
}
