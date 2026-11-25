package site.kvist.cloud

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.util.Base64

/**
 * What the page is handed when a photo is dragged onto the app from another one (split screen, a tablet's taskbar,
 * a desktop mode). A WebView does not pass another app's files to the page as a drop, so the app reads them and calls
 * `window.kvistDrop` (cloud/mobile/js/attachments.js), which treats them like photos picked with "+".
 */
object Photos {
    // The page redraws every photo at 2048 px and refuses one over 5 MB after that; a camera's original may be far larger.
    const val MAX_BYTES = 25 * 1024 * 1024
    const val MAX_COUNT = 8

    class Dropped(val name: String, val type: String, val bytes: ByteArray)

    fun isImage(mime: String?): Boolean = mime != null && mime.lowercase().startsWith("image/")

    /** A drag the app takes over: one that carries a picture. Text and links stay the WebView's own. */
    fun takesDrag(mimes: List<String>): Boolean = mimes.any(::isImage)

    /** The whole stream, or null when it is larger than a photo may be: nothing more than that is ever held. */
    fun readCapped(input: InputStream, max: Int = MAX_BYTES): ByteArray? {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) return out.toByteArray()
            if (out.size() + read > max) return null
            out.write(buffer, 0, read)
        }
    }

    fun dropScript(photos: List<Dropped>): String {
        val items = JSONArray()
        for (photo in photos.take(MAX_COUNT)) {
            items.put(JSONObject()
                .put("name", photo.name)
                .put("type", photo.type)
                .put("data", Base64.getEncoder().encodeToString(photo.bytes)))
        }
        return "window.kvistDrop && window.kvistDrop($items)"
    }

    fun draggingScript(on: Boolean): String = "window.kvistDragging && window.kvistDragging($on)"
}
