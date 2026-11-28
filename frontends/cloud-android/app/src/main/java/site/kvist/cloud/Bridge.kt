package site.kvist.cloud

import org.json.JSONException
import org.json.JSONObject

/** One request from the page: `KvistHost.call(id, name, json)`. */
data class Call(val id: String, val name: String, val args: JSONObject)

/**
 * The words the page and the app say to each other. The page (cloud/mobile/js/native.js) sends
 * {"id", "name", "json"} through a message channel that exists only for the cloud's own origin; the app answers
 * {"id", "payload"}. Pure, so the unit tests need no phone.
 */
object Bridge {
    /**
     * Runs in the page before its own scripts: makes `window.KvistHost` out of the message channel. `store`: the
     * Google Play build, in which the page sells nothing (cloud/mobile/js/native.js, storeBuild).
     */
    fun startScript(store: Boolean): String = """
        (function () {
          if (window.KvistHost || !window.KvistBridge) return;
          var port = window.KvistBridge;
          window.KvistHost = {
            platform: "android",
            store: $store,
            call: function (id, name, json) {
              port.postMessage(JSON.stringify({ id: id, name: name, json: json }));
            }
          };
          port.onmessage = function (event) {
            var reply = JSON.parse(event.data);
            if (window.__kvistReply) window.__kvistReply(reply.id, reply.payload);
          };
        })();
    """.trimIndent()

    /** The channel's name inside the page, and what the page calls the host. */
    const val CHANNEL = "KvistBridge"

    fun parse(message: String?): Call? {
        if (message == null) return null
        return try {
            val outer = JSONObject(message)
            val id = outer.optString("id")
            val name = outer.optString("name")
            if (id.isEmpty() || name.isEmpty()) return null
            // `json` is the page's own JSON.stringify of the arguments; a page that sent none sent "{}".
            Call(id, name, JSONObject(outer.optString("json", "{}").ifEmpty { "{}" }))
        } catch (error: JSONException) {
            null
        }
    }

    /** `payload` is any JSON value as text, or null for "nothing to say". */
    fun reply(id: String, payload: Any?): String =
        JSONObject().put("id", id).put("payload", payload ?: JSONObject.NULL).toString()

    fun cancelled(): JSONObject = JSONObject().put("cancelled", true)

    fun failed(code: String): JSONObject = JSONObject().put("error", code)

    fun token(token: String): JSONObject = JSONObject().put("token", token)
}
