package site.kvist.cloud

import java.net.URI
import java.net.URISyntaxException

/**
 * Where the WebView may go. The cloud itself, and GitHub while the person signs in (the cloud's own sign-in is GitHub's
 * page, which must load in this window for the session cookie to come back here). Everything else (a repository, a
 * payment page, the terms) opens in the system browser: this app is not a browser, and the payment bridge must never
 * be reachable from a page that is not ours.
 */
object Origins {
    private val SIGN_IN_HOSTS = setOf("github.com", "www.github.com")

    /** "https://cloud.example:8443" for a URL, or null when it is not an http(s) address. */
    fun originOf(url: String): String? {
        val uri = parse(url) ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        if (scheme != "https" && scheme != "http") return null
        val port = uri.port
        val standard = (scheme == "https" && port == 443) || (scheme == "http" && port == 80)
        return if (port == -1 || standard) "$scheme://$host" else "$scheme://$host:$port"
    }

    fun isCloud(url: String, cloudUrl: String): Boolean = originOf(url) != null && originOf(url) == originOf(cloudUrl)

    /** True when the WebView may load [url] itself. */
    fun staysInApp(url: String, cloudUrl: String): Boolean {
        if (isCloud(url, cloudUrl)) return true
        val uri = parse(url) ?: return false
        return uri.scheme?.lowercase() == "https" && uri.host?.lowercase() in SIGN_IN_HOSTS
    }

    /** Only web links go out to other apps: never an intent: URL or a file: path a page makes up. */
    fun mayOpenOutside(url: String): Boolean = parse(url)?.scheme?.lowercase().let { it == "https" || it == "http" || it == "mailto" }

    private fun parse(url: String): URI? = try {
        URI(url)
    } catch (error: URISyntaxException) {
        null
    }
}
