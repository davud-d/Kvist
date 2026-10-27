package com.kvist.intellij.chat

import com.google.gson.Gson
import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.ui.ColorUtil
import com.intellij.ui.JBColor
import com.intellij.ui.scale.JBUIScale
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.NamedColorUtil
import com.intellij.util.ui.UIUtil
import java.awt.Color
import java.security.SecureRandom
import java.util.Base64

/**
 * The page the JCEF browser shows: the VS Code webview bundle (`webview.js`, `webview.css`,
 * the logo), which the Gradle build copies from `frontends/ide/vscode/out/`, inlined into one
 * HTML document together with the host bridge the bundle's `host.ts` looks for.
 */
internal object KvistWebviewPage {
    private const val RESOURCE_ROOT = "/kvist-webview"
    private val gson = Gson()
    private val random = SecureRandom()

    /** [postCall] is JavaScript that sends the string variable `json` to the plugin. */
    fun html(postCall: String, workspacePage: Boolean = false): String {
        val script = inlineSafe(resource("webview.js"), "script")
        val style = inlineSafe(resource("webview.css"), "style")
        val logo = "data:image/png;base64," + Base64.getEncoder().encodeToString(resourceBytes("kvist-logo.png"))
        val nonce = nonce()
        val csp = listOf(
            "default-src 'none'",
            "style-src 'nonce-$nonce'",
            "script-src 'nonce-$nonce'",
            "img-src data: http://127.0.0.1:*",
        ).joinToString("; ")
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="$csp">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style nonce="$nonce">$style</style>
<title>Kvist</title>
</head>
<body class="${if (workspacePage) "workspace-page" else ""}">
<div id="root" data-logo="$logo" data-layout="${if (workspacePage) "workspace" else "panel"}"></div>
<script nonce="$nonce">${bridge(postCall)}</script>
<script nonce="$nonce">$script</script>
</body>
</html>"""
    }

    /** Re-applies the IDE theme to an open page, e.g. after the look and feel changed. */
    fun themeCall(): String = "window.kvistHost && window.kvistHost.theme(${gson.toJson(themeVariables())}, ${JBColor.isBright()});"

    private fun bridge(postCall: String): String = """
(function () {
  window.kvistHost = {
    post: function (json) { $postCall },
    theme: function (variables, light) {
      var style = document.documentElement.style;
      Object.keys(variables).forEach(function (name) { style.setProperty(name, variables[name]); });
      document.body.classList.toggle("vscode-light", light);
    }
  };
  ${themeCall()}
})();
"""

    // The stylesheet is written against VS Code's theme variables; these are their IDE counterparts.
    private fun themeVariables(): Map<String, String> {
        val scheme = EditorColorsManager.getInstance().globalScheme
        val labelFont = UIUtil.getLabelFont()
        val userScale = JBUIScale.scale(1f)
        return mapOf(
            "--vscode-foreground" to css(UIUtil.getLabelForeground()),
            "--vscode-descriptionForeground" to css(UIUtil.getContextHelpForeground()),
            "--vscode-errorForeground" to css(NamedColorUtil.getErrorForeground()),
            "--vscode-editorWarning-foreground" to css(JBColor(0xB08800, 0xF5D547)),
            "--vscode-textLink-foreground" to css(JBUI.CurrentTheme.Link.Foreground.ENABLED),
            "--vscode-focusBorder" to css(JBUI.CurrentTheme.Focus.focusColor()),
            "--vscode-sideBar-background" to css(UIUtil.getPanelBackground()),
            "--vscode-editor-background" to css(scheme.defaultBackground),
            "--vscode-textCodeBlock-background" to css(scheme.defaultBackground),
            "--vscode-editorSuggestWidget-background" to css(JBColor.namedColor("Popup.background", UIUtil.getListBackground())),
            "--vscode-input-background" to css(UIUtil.getTextFieldBackground()),
            "--vscode-dropdown-background" to css(UIUtil.getListBackground()),
            "--vscode-dropdown-foreground" to css(UIUtil.getListForeground()),
            "--vscode-font-family" to "\"${labelFont.family}\", system-ui, sans-serif",
            "--vscode-font-size" to "${Math.round(labelFont.size2D / userScale)}px",
            "--vscode-editor-font-family" to "\"${scheme.editorFontName}\", monospace",
            "--vscode-editor-font-size" to "${scheme.editorFontSize}px",
        )
    }

    private fun css(color: Color): String = "#" + ColorUtil.toHex(color)

    // The bundle is inlined, so nothing in it may close its own element early.
    private fun inlineSafe(text: String, element: String): String {
        return text
            .replace(Regex("^/[/*]# sourceMappingURL=.*$", RegexOption.MULTILINE), "")
            .replace(Regex("</($element)", RegexOption.IGNORE_CASE), "<\\\\/$1")
    }

    private fun resource(name: String): String = String(resourceBytes(name), Charsets.UTF_8)

    private fun resourceBytes(name: String): ByteArray {
        val stream = KvistWebviewPage::class.java.getResourceAsStream("$RESOURCE_ROOT/$name")
            ?: throw IllegalStateException("The Kvist panel files are missing from this plugin build ($name).")
        return stream.use { it.readBytes() }
    }

    private fun nonce(): String {
        val bytes = ByteArray(24)
        random.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}
