package com.kvist.intellij.chat

import com.sun.net.httpserver.HttpServer
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/**
 * The panel's cloud half: which Kvist Cloud it shows, and signing in to it for the framed cloud page
 * (cloud/backend/kvist_cloud/appauth.py). A port of `frontends/ide/vscode/src/cloudSignIn.ts`: GitHub's page refuses
 * frames, so the system browser signs in, the cloud hands a short-lived code to a listener on this computer
 * (RFC 8252 loopback), and the framed page trades the code, with a nonce only this plugin knows, for its session.
 */
internal object KvistCloud {
    private const val DEFAULT_URL = "https://cloud.kvist.site"
    private const val WAIT_MINUTES = 5L
    private val random = SecureRandom()
    private val DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Kvist Cloud</title>
<body style="font:16px system-ui;background:#0a0a0b;color:#e8e8ea;display:grid;place-items:center;height:90vh">
<p>Signed in. Go back to the IDE. / Вход выполнен, вернись в IDE.</p></body>""".toByteArray(StandardCharsets.UTF_8)

    /** An origin, or "" (no cloud half) when `-Dkvist.cloud.url` / `KVIST_CLOUD_URL` is not an http(s) address. */
    fun origin(): String {
        val raw = System.getProperty("kvist.cloud.url") ?: System.getenv("KVIST_CLOUD_URL") ?: DEFAULT_URL
        val uri = try {
            URI(raw.trim())
        }
        catch (_: URISyntaxException) {
            return ""
        }
        if (uri.scheme != "https" && uri.scheme != "http" || uri.host.isNullOrEmpty()) return ""
        return "${uri.scheme}://${uri.host}${if (uri.port == -1) "" else ":${uri.port}"}"
    }

    /**
     * Opens the cloud's sign-in with [openBrowser] and blocks until the browser comes back, at most five minutes;
     * returns the address the framed page loads to get its session. Call it off the UI thread.
     */
    fun signIn(origin: String, openBrowser: (String) -> Unit): String {
        val nonce = nonce()
        val code = CompletableFuture<String>()
        val server = HttpServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0)
        server.createContext("/signed-in") { exchange ->
            val received = queryValue(exchange.requestURI.rawQuery, "code")
            if (received == null) {
                exchange.sendResponseHeaders(404, -1)
                exchange.close()
                return@createContext
            }
            exchange.responseHeaders.add("Content-Type", "text/html; charset=utf-8")
            exchange.sendResponseHeaders(200, DONE_PAGE.size.toLong())
            exchange.responseBody.use { it.write(DONE_PAGE) }
            code.complete(received)
        }
        server.start()
        try {
            openBrowser("$origin/auth/app/start?nonce=${encode(nonce)}&port=${server.address.port}")
            val value = code.get(WAIT_MINUTES, TimeUnit.MINUTES)
            return "$origin/auth/app/exchange?code=${encode(value)}&nonce=${encode(nonce)}&next=${encode("/m/")}"
        }
        finally {
            server.stop(0)
        }
    }

    private fun queryValue(rawQuery: String?, name: String): String? {
        return rawQuery.orEmpty().split("&")
            .map { it.split("=", limit = 2) }
            .firstOrNull { it.size == 2 && it[0] == name }
            ?.let { URLDecoder.decode(it[1], StandardCharsets.UTF_8) }
            ?.takeIf { it.isNotEmpty() }
    }

    private fun encode(value: String): String = URLEncoder.encode(value, StandardCharsets.UTF_8)

    private fun nonce(): String {
        val bytes = ByteArray(32)
        random.nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }
}
