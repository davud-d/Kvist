package com.kvist.intellij.chat

import com.intellij.ide.ui.LafManagerListener
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Disposer
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ui.jcef.JBCefBrowserBase
import com.intellij.ui.jcef.JBCefJSQuery
import javax.swing.JComponent

/** The VS Code panel in a JCEF browser; the page reaches the plugin through one JBCefJSQuery. */
internal class KvistChatPanel(project: Project, private val workspacePage: Boolean = false) : Disposable {
    private val browser = JBCefBrowser()
    // Created before the page loads, so its function exists when the bundle posts `ready`.
    private val query = JBCefJSQuery.create(browser as JBCefBrowserBase)
    val host = KvistChatHost(project) { script -> browser.cefBrowser.executeJavaScript(script, browser.cefBrowser.url, 0) }

    val component: JComponent
        get() = browser.component

    init {
        Disposer.register(this, browser)
        Disposer.register(this, query)
        Disposer.register(this, host)
        query.addHandler { request ->
            host.onPageMessage(request)
            null
        }
        ApplicationManager.getApplication().messageBus.connect(this)
            .subscribe(LafManagerListener.TOPIC, LafManagerListener { applyTheme() })
        try {
            load()
        }
        catch (exception: IllegalStateException) {
            Disposer.dispose(this)
            throw exception
        }
    }

    // The page's `ready` starts a new core, so this is also how an exited core is restarted.
    fun load() {
        host.pageLoading()
        browser.loadHTML(KvistWebviewPage.html(query.inject("json"), workspacePage))
    }

    private fun applyTheme() {
        browser.cefBrowser.executeJavaScript(KvistWebviewPage.themeCall(), browser.cefBrowser.url, 0)
    }

    override fun dispose() = Unit
}
