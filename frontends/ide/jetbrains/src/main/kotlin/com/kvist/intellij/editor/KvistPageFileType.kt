package com.kvist.intellij.editor

import com.intellij.openapi.fileTypes.FileType
import com.intellij.openapi.util.IconLoader
import javax.swing.Icon

/**
 * The type of the Kvist page's virtual file. The editor tab, the breadcrumbs and the recent-files
 * list differ between IDE versions in whether they ask a FileIconProvider or the file's own type,
 * so the type carries the icon too. plugin.xml also claims the exact name "Kvist": a tab first shows
 * the icon its file name implies and only later the one the file itself reports.
 */
object KvistPageFileType : FileType {
    override fun getName() = "Kvist"
    override fun getDescription() = "Kvist page"
    override fun getDefaultExtension() = ""
    override fun getIcon(): Icon = IconLoader.getIcon("/icons/kvist.svg", KvistPageFileType::class.java)
    // Not binary: the IDE then gives the file an (empty) document, which the headless editor manager of the tests needs.
    override fun isBinary() = false
    override fun isReadOnly() = true
}
