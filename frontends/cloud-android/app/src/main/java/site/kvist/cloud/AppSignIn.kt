package site.kvist.cloud

import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.SecureRandom

/**
 * Signing in from the app (cloud/backend/kvist_cloud/appauth.py). GitHub's page, and Google's behind "sign in with
 * Google", will not run in a web view, so a Custom Tab (the browser's own session) does it and hands the app a
 * short-lived code; the web view trades the code (and the nonce only this app knows) for its session cookie.
 */
object AppSignIn {
    const val SCHEME = "kvistcloud"
    private const val HOST = "signed-in"

    /** 32 random bytes as hex: made fresh for every attempt. */
    fun makeNonce(random: SecureRandom = SecureRandom()): String =
        ByteArray(32).also(random::nextBytes).joinToString("") { "%02x".format(it) }

    private fun encode(value: String) = URLEncoder.encode(value, "UTF-8")

    /** What the Custom Tab opens. */
    fun startUrl(cloudUrl: String, nonce: String) = "$cloudUrl/auth/app/start?nonce=${encode(nonce)}"

    /** The code in the address the browser ends on (kvistcloud://signed-in?code=...), or null for anything else. */
    fun codeFrom(callback: String?): String? {
        if (callback == null) return null
        val uri = try {
            URI(callback)
        } catch (error: URISyntaxException) {
            return null
        }
        if (uri.scheme?.lowercase() != SCHEME || uri.host?.lowercase() != HOST) return null
        val code = uri.rawQuery?.split("&")?.map { it.split("=", limit = 2) }
            ?.firstOrNull { it[0] == "code" }?.getOrNull(1)?.let { URLDecoder.decode(it, "UTF-8") }
        return code?.takeIf { it.isNotEmpty() }
    }

    /** What the web view loads to get its cookie, and then lands on the app's start page. */
    fun exchangeUrl(cloudUrl: String, code: String, nonce: String) =
        "$cloudUrl/auth/app/exchange?code=${encode(code)}&nonce=${encode(nonce)}&next=${encode("/m/")}"
}
