package site.kvist.cloud

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject

/**
 * One WebView with Kvist Cloud's phone page in it, and the operating system's own things the page cannot do:
 * the Google Pay sheet, links in the system browser, the clipboard, a tap's vibration.
 *
 * The page reaches this class only through a message channel that exists for the cloud's own origin (Bridge.START_SCRIPT
 * makes it look like `window.KvistHost`); GitHub's sign-in page and anything else loaded here get nothing.
 */
class MainActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var root: FrameLayout
    private lateinit var offline: View
    private lateinit var pay: GooglePay
    private val cloudUrl = BuildConfig.CLOUD_URL
    private var failed = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pay = GooglePay(this)
        webView = WebView(this)
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) WebView.setWebContentsDebuggingEnabled(true)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            userAgentString = "$userAgentString KvistCloudApp/1"
        }
        webView.setBackgroundColor(getColor(R.color.kvist_bg))
        webView.webViewClient = client()
        installBridge()

        offline = offlineView()
        root = FrameLayout(this).apply {
            setBackgroundColor(getColor(R.color.kvist_bg))
            addView(webView, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
            addView(offline, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        offline.visibility = View.GONE
        setContentView(root)
        fitSystemBars()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // The page closes a sheet or the drawer first, then goes up to the start; only at the top does the app leave.
                webView.evaluateJavascript("(window.kvistBack && window.kvistBack()) === true") { answer ->
                    if (answer != "true") moveTaskToBack(true)
                }
            }
        })
        webView.loadUrl("$cloudUrl/m/")
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    // ---- the bridge ---------------------------------------------------------------------------------------

    private fun installBridge() {
        val supported = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) &&
            WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
        if (!supported) {
            // The page still works; it just sees no wallet and offers the other ways to pay.
            Toast.makeText(this, R.string.update_webview, Toast.LENGTH_LONG).show()
            return
        }
        val origins = setOf(Origins.originOf(cloudUrl)!!)
        WebViewCompat.addWebMessageListener(webView, Bridge.CHANNEL, origins) { _, message, _, _, reply ->
            handle(message, reply)
        }
        WebViewCompat.addDocumentStartJavaScript(webView, Bridge.START_SCRIPT, origins)
    }

    private fun handle(message: WebMessageCompat, reply: JavaScriptReplyProxy) {
        val call = Bridge.parse(message.data) ?: return
        val answer = { payload: Any? -> reply.postMessage(Bridge.reply(call.id, payload)) }
        when (call.name) {
            "wallets" -> pay.isReady { ready -> answer(JSONArray(if (ready) listOf("google_pay") else emptyList<String>())) }
            "pay" -> pay.pay(call.args) { result -> answer(result) }
            "open" -> {
                open(call.args.optString("url"))
                answer(null)
            }
            "haptic" -> {
                haptic(call.args.optString("kind"))
                answer(null)
            }
            "copy" -> {
                copy(call.args.optString("text"))
                answer(null)
            }
            "share" -> {
                share(call.args.optString("text"))
                answer(null)
            }
            else -> answer(null)
        }
    }

    private fun open(url: String) {
        if (!Origins.mayOpenOutside(url)) return
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE))
        } catch (error: ActivityNotFoundException) {
            Toast.makeText(this, url, Toast.LENGTH_LONG).show()
        }
    }

    private fun haptic(kind: String) {
        val effect = when (kind) {
            "success" -> HapticFeedbackConstants.CONFIRM
            "warn" -> HapticFeedbackConstants.REJECT
            else -> HapticFeedbackConstants.VIRTUAL_KEY
        }
        webView.performHapticFeedback(effect)
    }

    private fun copy(text: String) {
        val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("Kvist Cloud", text))
    }

    private fun share(text: String) {
        val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
        startActivity(Intent.createChooser(send, getString(R.string.share)))
    }

    // ---- the page and its failures ----------------------------------------------------------------------

    private fun client() = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            if (Origins.staysInApp(url, cloudUrl)) return false
            open(url)
            return true
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            failed = true
            offline.visibility = View.VISIBLE
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (!failed) offline.visibility = View.GONE
        }

        // A WebView whose renderer died would stay blank: start over from the cloud's page.
        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            recreate()
            return true
        }
    }

    private fun offlineView(): View {
        val padding = (24 * resources.displayMetrics.density).toInt()
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(padding, padding, padding, padding)
            setBackgroundColor(getColor(R.color.kvist_bg))
            isClickable = true
            addView(TextView(context).apply {
                setText(R.string.offline_title)
                textSize = 22f
                setTextColor(getColor(R.color.kvist_text))
                gravity = Gravity.CENTER
            })
            addView(TextView(context).apply {
                setText(R.string.offline_body)
                textSize = 16f
                setTextColor(getColor(R.color.kvist_text_dim))
                gravity = Gravity.CENTER
                setPadding(0, padding / 2, 0, padding)
            })
            addView(Button(context).apply {
                setText(R.string.retry)
                setOnClickListener {
                    failed = false
                    visibility = View.VISIBLE
                    webView.loadUrl("$cloudUrl/m/")
                }
            })
        }
    }

    /** The page is laid out between the bars, on the bars' own dark colour; the keyboard resizes it (adjustResize). */
    private fun fitSystemBars() {
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard.bottom))
            WindowInsetsCompat.CONSUMED
        }
    }
}
