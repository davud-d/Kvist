package site.kvist.android

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64

/** The saved computer: its name, where it was last seen and the code that opens it. */
data class Saved(val name: String, val host: String, val port: Int, val code: String)

sealed interface PairResult {
    data object Ok : PairResult
    data object WrongCode : PairResult
    data class Unreachable(val reason: String) : PairResult
}

object Pairing {
    private const val CONNECT_TIMEOUT_MS = 3000
    private const val READ_TIMEOUT_MS = 4000

    /** The code is read off a screen and typed: case, dashes and spaces do not matter. */
    fun normalize(code: String): String = code.uppercase().filter { it.isLetterOrDigit() }

    fun authHeader(code: String): String = "Basic " + Base64.getEncoder().encodeToString("kvist:$code".toByteArray(Charsets.UTF_8))

    /** Blocking: asks the computer for its page with the code. Call it off the main thread. */
    fun check(host: String, port: Int, code: String): PairResult {
        val connection = URL("http://$host:$port/").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Authorization", authHeader(code))
            return when (val status = connection.responseCode) {
                HttpURLConnection.HTTP_OK -> PairResult.Ok
                HttpURLConnection.HTTP_UNAUTHORIZED -> PairResult.WrongCode
                else -> PairResult.Unreachable("HTTP $status")
            }
        } catch (error: IOException) {
            return PairResult.Unreachable(error.message ?: error.javaClass.simpleName)
        } finally {
            connection.disconnect()
        }
    }
}
