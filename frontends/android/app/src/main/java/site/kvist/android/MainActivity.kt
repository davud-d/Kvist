package site.kvist.android

import android.annotation.SuppressLint
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.WindowInsets
import android.webkit.HttpAuthHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.net.Inet4Address
import java.net.InetAddress
import java.util.concurrent.Executors

/**
 * One WebView and no agent. Two pages live in it: the local connect screen (find a computer, enter its code)
 * and, once paired, the page that computer serves at /m/. The connect screen talks to this class through a
 * message channel that exists only for the app's own origin, so the computer's page cannot reach it.
 */
class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var root: FrameLayout
    private lateinit var store: Store
    private lateinit var assets: WebViewAssetLoader
    private val network = Executors.newSingleThreadExecutor()

    private var current: Saved? = null
    private var authAsked = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        assets = WebViewAssetLoader.Builder()
            .setDomain(ASSET_HOST)
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        webView = WebView(this)
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) WebView.setWebContentsDebuggingEnabled(true)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            // The computer's page shows a "change computer" link only inside this app.
            userAgentString = "$userAgentString KvistApp/1"
        }
        webView.webViewClient = client()
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            Toast.makeText(this, "Update Android System WebView", Toast.LENGTH_LONG).show()
        } else {
            WebViewCompat.addWebMessageListener(webView, "KvistNative", setOf(ASSET_ORIGIN)) { _, message, _, _, proxy ->
                onMessage(message, proxy)
            }
        }
        root = FrameLayout(this).apply {
            setBackgroundColor(getColor(R.color.kvist_bg))
            addView(webView, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        setContentView(root)
        fitSystemBars()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.url?.startsWith("http://") != true) {
                    finish()
                    return
                }
                // The computer's page keeps its own screens in the address: a session is a hash, the list is none.
                webView.evaluateJavascript("(function(){ if (location.hash.length > 1) { history.back(); return 'back'; } return 'top'; })()") { answer ->
                    if (answer.trim('"') != "back") moveTaskToBack(true)
                }
            }
        })
        val saved = store.load()
        if (saved == null) showConnect() else openComputer(saved)
    }

    override fun onDestroy() {
        network.shutdownNow()
        super.onDestroy()
    }

    // ---- pages ---------------------------------------------------------------------------------------

    private fun showConnect(error: String? = null, detail: String? = null) {
        current = null
        val query = when {
            error == null -> ""
            detail == null -> "?err=$error"
            else -> "?err=$error&detail=${Uri.encode(detail)}"
        }
        webView.loadUrl("$ASSET_ORIGIN/assets/connect.html$query")
    }

    private fun openComputer(saved: Saved) {
        current = saved
        authAsked = false
        store.save(saved)
        webView.loadUrl("http://${saved.host}:${saved.port}/m/")
    }

    private fun allowed(uri: Uri): Boolean {
        if (uri.scheme == "https" && uri.host == ASSET_HOST) return true
        val saved = current ?: return false
        return uri.scheme == "http" && uri.host == saved.host && (if (uri.port == -1) 80 else uri.port) == saved.port
    }

    private fun client() = object : WebViewClient() {
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
            assets.shouldInterceptRequest(request.url)

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val uri = request.url
            if (uri.scheme == "kvist-app" && uri.host == "switch") {
                store.forget()
                showConnect()
                return true
            }
            // Only the paired computer and the app's own pages: the phone is not a browser.
            return !allowed(uri)
        }

        override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, realm: String?) {
            val saved = current
            if (saved == null || host != saved.host || authAsked) {
                // The computer no longer knows this phone (it was forgotten there): ask again from the list.
                handler.cancel()
                store.forget()
                showConnect("forgotten")
                return
            }
            authAsked = true
            handler.proceed("kvist", saved.token)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame && request.url.host == current?.host) showConnect("unreachable", error.description.toString())
        }

        override fun onPageFinished(view: WebView, url: String) {
            view.evaluateJavascript("(document.querySelector('meta[name=theme-color]')||{}).content||''") { value ->
                val color = value.trim('"')
                if (color.startsWith("#")) runCatching { root.setBackgroundColor(Color.parseColor(color)) }
            }
        }
    }

    // ---- the connect screen's channel --------------------------------------------------------------------

    private fun onMessage(message: WebMessageCompat, proxy: JavaScriptReplyProxy) {
        val request = try {
            JSONObject(message.data ?: return)
        } catch (error: JSONException) {
            return
        }
        when (request.optString("op")) {
            "saved" -> send(proxy, JSONObject().put("type", "saved").put("saved", store.load()?.let { it.toJson() } ?: JSONObject.NULL))
            "scan" -> network.execute { send(proxy, scan()) }
            "pair" -> network.execute { send(proxy, pair(request)) }
            "forget" -> store.forget()
        }
    }

    private fun send(proxy: JavaScriptReplyProxy, json: JSONObject) {
        runOnUiThread { proxy.postMessage(json.toString()) }
    }

    private fun scan(): JSONObject {
        val saved = store.load()
        val list = JSONArray()
        for (computer in Discovery.scan(broadcastTargets(), SCAN_WINDOW_MS)) {
            list.put(
                JSONObject()
                    .put("id", computer.id).put("name", computer.name).put("host", computer.host).put("port", computer.port)
                    .put("os", computer.os).put("os_name", computer.osName).put("user", computer.user)
                    .put("saved", computer.id == saved?.id),
            )
        }
        return JSONObject().put("type", "found").put("computers", list)
    }

    /**
     * A computer this phone is trusted by opens at once, wherever the router put it now. Otherwise the computer
     * asks its owner and this waits for the answer. `auto` is the app starting up by itself: it must not make a
     * window appear on the computer, so it never asks.
     */
    private fun pair(request: JSONObject): JSONObject {
        val host = request.optString("host").trim()
        val port = request.optInt("port", 9671)
        val id = request.optString("id").ifBlank { host }
        val name = request.optString("name").ifBlank { host }
        val saved = store.load()
        if (host.isEmpty()) return failed("unreachable")
        if (saved != null && saved.id == id) {
            when (val trust = Pairing.trusted(host, port, saved.token)) {
                Trust.Yes -> return opened(Saved(id, name, host, port, saved.token))
                is Trust.Unreachable -> return failed("unreachable").put("detail", "$host:$port ${trust.reason}")
                Trust.No -> if (request.optBoolean("auto")) return failed("forgotten")
            }
        } else if (request.optBoolean("auto")) {
            return failed("forgotten")
        }
        return when (val asked = Pairing.ask(host, port, store.deviceId(), Build.MODEL)) {
            is Asked.Refused -> failed(asked.reason)
            is Asked.Failed -> failed("unreachable").put("detail", "$host:$port ${asked.reason}")
            is Asked.Id -> waitForOwner(Saved(id, name, host, port, ""), asked.value)
        }
    }

    private fun waitForOwner(computer: Saved, requestId: String): JSONObject {
        repeat(ANSWER_POLLS) {
            when (val answer = Pairing.poll(computer.host, computer.port, requestId)) {
                is Answer.Allowed -> return opened(computer.copy(token = answer.token))
                Answer.Denied -> return failed("denied")
                Answer.Unavailable -> return failed("unavailable")
                Answer.Expired -> return failed("expired")
                Answer.Pending -> Thread.sleep(POLL_MS)
            }
        }
        return failed("expired")
    }

    private fun opened(saved: Saved): JSONObject {
        runOnUiThread { openComputer(saved) }
        return JSONObject().put("type", "paired")
    }

    private fun failed(reason: String): JSONObject = JSONObject().put("type", "pair-failed").put("reason", reason)

    private fun broadcastTargets(): List<InetAddress> {
        val targets = mutableListOf<InetAddress>(InetAddress.getByAddress(byteArrayOf(-1, -1, -1, -1)))
        val connectivity = getSystemService(ConnectivityManager::class.java)
        val properties = connectivity.getLinkProperties(connectivity.activeNetwork) ?: return targets
        for (link in properties.linkAddresses) {
            val address = link.address
            if (address is Inet4Address) targets += Discovery.broadcastOf(address.address, link.prefixLength)
        }
        return targets.distinct()
    }

    private fun Saved.toJson(): JSONObject = JSONObject().put("id", id).put("name", name).put("host", host).put("port", port)

    // ---- edge to edge ------------------------------------------------------------------------------------------

    // From Android 15 an app draws under the system bars; the pages are padded instead of reading insets themselves.
    private fun fitSystemBars() {
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                view.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop, insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
            }
            WindowInsets.CONSUMED
        }
    }

    private companion object {
        const val ASSET_HOST = "appassets.androidplatform.net"
        const val ASSET_ORIGIN = "https://$ASSET_HOST"
        const val SCAN_WINDOW_MS = 1600L
        // The owner has a minute to answer; the computer's window gives up after the same time.
        const val POLL_MS = 1000L
        const val ANSWER_POLLS = 75
    }
}
