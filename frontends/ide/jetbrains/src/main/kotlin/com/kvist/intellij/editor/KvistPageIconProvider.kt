package com.kvist.intellij.editor

import com.intellij.ide.FileIconProvider
import com.intellij.ide.IconProvider
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiFile
import com.intellij.psi.util.PsiUtilCore
import javax.swing.Icon

/**
 * The Kvist page is not a document, so its tab and breadcrumbs show the Kvist mark instead of a file icon.
 * Icon packs (Catppuccin Icons, Material Icon Theme) answer for every PSI file and take the first turn,
 * so plugin.xml ranks this one ahead of them.
 */
class KvistPageIconProvider : IconProvider(), FileIconProvider, DumbAware {
    override fun getIcon(element: PsiElement, flags: Int): Icon? =
        if (element is PsiFile && PsiUtilCore.getVirtualFile(element) is KvistPageFile) KvistPageFileType.icon else null

    override fun getIcon(file: VirtualFile, flags: Int, project: Project?): Icon? =
        if (file is KvistPageFile) KvistPageFileType.icon else null
}
