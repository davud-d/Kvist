package site.kvist.cloud

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.view.DragEvent
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.View
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
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
import androidx.activity.result.contract.ActivityResultContracts
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException

/**
 * One WebView with Kvist Cloud's phone page in it, and the operating system's own things the page cannot do:
 * the Google Pay sheet, links in the system browser, the clipboard, a tap's vibration, the photo picker and the camera
 * behind the page's file inputs, and photos dragged onto the app from another one.
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
    // The nonce of the sign-in in the browser right now: only this app knows it, and the cloud wants it back with the code.
    private var signInNonce: String? = null
    // The page's file input waiting for the picker or the camera, and where the camera writes its photo.
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var cameraUri: Uri? = null
    private val chooser = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        deliverPicked(result.resultCode, result.data)
    }

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
        webView.webChromeClient = chromeClient()
        webView.setOnDragListener { _, event -> onDrag(event) }
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
        handleSignInResult(intent)
    }

    // singleTask: the browser's last redirect (kvistcloud://signed-in?code=...) arrives here.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleSignInResult(intent)
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
            "signIn" -> {
                signIn()
                answer(org.json.JSONObject().put("started", true))
            }
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

    /** GitHub's page, and Google's behind "sign in with Google", will not run in this web view: a Custom Tab does it. */
    private fun signIn() {
        val nonce = AppSignIn.makeNonce()
        signInNonce = nonce
        try {
            CustomTabsIntent.Builder().build().launchUrl(this, Uri.parse(AppSignIn.startUrl(cloudUrl, nonce)))
        } catch (error: ActivityNotFoundException) {
            signInNonce = null
            webView.loadUrl("$cloudUrl/auth/github/login?next=%2Fm%2F")
        }
    }

    private fun handleSignInResult(intent: Intent?) {
        val code = AppSignIn.codeFrom(intent?.dataString) ?: return
        val nonce = signInNonce ?: return
        signInNonce = null
        webView.loadUrl(AppSignIn.exchangeUrl(cloudUrl, code, nonce))
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

    // ---- photos: the page's file inputs, and drops from other apps ----------------------------------------

    /** A WebView ignores `<input type=file>` unless the app opens a picker: the gallery, or the camera for `capture`. */
    private fun chromeClient() = object : WebChromeClient() {
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            fileCallback?.onReceiveValue(null)
            fileCallback = callback
            val intent = if (params.isCaptureEnabled) {
                cameraIntent()
            } else {
                params.createIntent().putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
            }
            try {
                chooser.launch(intent)
            } catch (error: ActivityNotFoundException) {
                deliverPicked(Activity.RESULT_CANCELED, null)
            }
            return true
        }
    }

    private fun cameraIntent(): Intent {
        // One photo at a time: whatever an earlier capture left behind goes first.
        val folder = File(cacheDir, "camera").apply { deleteRecursively(); mkdirs() }
        val uri = FileProvider.getUriForFile(this, "$packageName.photos", File(folder, "photo-${System.currentTimeMillis()}.jpg"))
        cameraUri = uri
        return Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            .putExtra(MediaStore.EXTRA_OUTPUT, uri)
            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }

    private fun deliverPicked(resultCode: Int, data: Intent?) {
        val callback = fileCallback ?: return
        fileCallback = null
        val camera = cameraUri
        cameraUri = null
        if (resultCode != Activity.RESULT_OK) {
            callback.onReceiveValue(null)
            return
        }
        val clip = data?.clipData
        val picked = when {
            camera != null -> arrayOf(camera)
            clip != null -> Array(clip.itemCount) { clip.getItemAt(it).uri }
            else -> data?.data?.let { arrayOf(it) }
        }
        callback.onReceiveValue(picked)
    }

    private fun onDrag(event: DragEvent): Boolean {
        when (event.action) {
            DragEvent.ACTION_DRAG_STARTED -> {
                val description = event.clipDescription ?: return false
                // false: a drag of text or a link is left to the WebView itself.
                return Photos.takesDrag((0 until description.mimeTypeCount).map(description::getMimeType))
            }
            DragEvent.ACTION_DRAG_ENTERED -> webView.evaluateJavascript(Photos.draggingScript(true), null)
            DragEvent.ACTION_DRAG_EXITED, DragEvent.ACTION_DRAG_ENDED -> webView.evaluateJavascript(Photos.draggingScript(false), null)
            DragEvent.ACTION_DROP -> readDrop(event)
        }
        return true
    }

    /** Another app's photos are readable only while the permission this drop grants is held: read them, then let go. */
    private fun readDrop(event: DragEvent) {
        val clip = event.clipData ?: return
        val permissions = requestDragAndDropPermissions(event)
        val uris = (0 until clip.itemCount).mapNotNull { clip.getItemAt(it).uri }
        Thread {
            val photos = uris.mapNotNull { readPhoto(it) }
            runOnUiThread {
                permissions?.release()
                if (photos.isNotEmpty()) webView.evaluateJavascript(Photos.dropScript(photos), null)
            }
        }.start()
    }

    private fun readPhoto(uri: Uri): Photos.Dropped? {
        val type = contentResolver.getType(uri)
        if (!Photos.isImage(type)) return null
        return try {
            val bytes = contentResolver.openInputStream(uri)?.use { Photos.readCapped(it) } ?: return null
            Photos.Dropped("photo", type!!, bytes)
        } catch (error: IOException) {
            null
        } catch (error: SecurityException) {
            // The other app took its permission back: that photo is skipped, the rest still arrive.
            null
        }
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
