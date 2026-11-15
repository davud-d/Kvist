package site.kvist.android

import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64

/** The trusted computer: who it is, where it was last seen and the token only this phone holds. */
data class Saved(val id: String, val name: String, val host: String, val port: Int, val token: String)

sealed interface Answer {
    /** The owner allowed this phone; the token is shown once. */
    data class Allowed(val token: String) : Answer
    data object Pending : Answer
    data object Denied : Answer

    /** The computer has no way to ask its owner (no desktop session). */
    data object Unavailable : Answer
    data object Expired : Answer
}

sealed interface Trust {
    data object Yes : Trust
    data object No : Trust

    /** The computer did not answer as a Kvist does; [reason] is what the network said. */
    data class Unreachable(val reason: String) : Trust
}

sealed interface Asked {
    data class Id(val value: String) : Asked

    /** "busy" (another phone is asking) or "cooldown" (the owner just said no). */
    data class Refused(val reason: String) : Asked
    data class Failed(val reason: String) : Asked
}

object Pairing {
    private const val CONNECT_TIMEOUT_MS = 3000
    private const val READ_TIMEOUT_MS = 4000

    fun authHeader(token: String): String = "Basic " + Base64.getEncoder().encodeToString("kvist:$token".toByteArray(Charsets.UTF_8))

    fun parseAnswer(body: String): Answer {
        val json = try {
            JSONObject(body)
        } catch (error: JSONException) {
            return Answer.Expired
        }
        return when (json.optString("state")) {
            "approved" -> json.optString("token").takeIf { it.isNotEmpty() }?.let { Answer.Allowed(it) } ?: Answer.Expired
            "pending" -> Answer.Pending
            "denied" -> Answer.Denied
            "unavailable" -> Answer.Unavailable
            else -> Answer.Expired
        }
    }

    /** Blocking: asks the computer to ask its owner. Call it off the main thread. */
    fun ask(host: String, port: Int, deviceId: String, deviceName: String): Asked {
        val body = JSONObject().put("device_id", deviceId).put("name", deviceName).toString()
        return try {
            val (status, text) = call(host, port, "POST", "/pair/request", body = body)
            val json = JSONObject(text)
            when {
                status == HttpURLConnection.HTTP_OK -> Asked.Id(json.getString("request_id"))
                status == 429 -> Asked.Refused(json.optString("error"))
                else -> Asked.Failed("HTTP $status")
            }
        } catch (error: IOException) {
            Asked.Failed(reasonOf(error))
        } catch (error: JSONException) {
            Asked.Failed("unreadable answer")
        }
    }

    /** Blocking: has the owner answered yet? */
    fun poll(host: String, port: Int, requestId: String): Answer = try {
        parseAnswer(call(host, port, "GET", "/pair/status?request_id=$requestId").second)
    } catch (error: IOException) {
        Answer.Pending
    }

    /** Blocking: does the computer still trust this phone? */
    fun trusted(host: String, port: Int, token: String): Trust = try {
        when (val status = call(host, port, "GET", "/", token = token).first) {
            HttpURLConnection.HTTP_OK -> Trust.Yes
            HttpURLConnection.HTTP_UNAUTHORIZED -> Trust.No
            else -> Trust.Unreachable("HTTP $status")
        }
    } catch (error: IOException) {
        Trust.Unreachable(reasonOf(error))
    }

    /** What a person can read to the one who helps: the kind of failure and the network's own words. */
    fun reasonOf(error: IOException): String = "${error.javaClass.simpleName}: ${error.message ?: "no message"}"

    private fun call(host: String, port: Int, method: String, path: String, body: String? = null, token: String? = null): Pair<Int, String> {
        val connection = URL("http://$host:$port$path").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.instanceFollowRedirects = false
            if (token != null) connection.setRequestProperty("Authorization", authHeader(token))
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status < 400) connection.inputStream else connection.errorStream
            return status to (stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: "")
        } finally {
            connection.disconnect()
        }
    }
}
