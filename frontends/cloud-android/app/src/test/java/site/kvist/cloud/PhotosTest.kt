package site.kvist.cloud

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class PhotosTest {
    @Test
    fun onlyADragThatCarriesAPictureIsTakenFromTheWebView() {
        assertTrue(Photos.takesDrag(listOf("text/plain", "image/jpeg")))
        assertTrue(Photos.takesDrag(listOf("IMAGE/PNG")))
        assertFalse(Photos.takesDrag(listOf("text/plain", "text/uri-list")))
        assertFalse(Photos.takesDrag(emptyList()))
    }

    @Test
    fun theDropReachesThePageAsDataItCanTurnBackIntoFiles() {
        val bytes = byteArrayOf(-1, -40, -1, 0, 39, 34)
        val script = Photos.dropScript(listOf(Photos.Dropped("a\"');x", "image/jpeg", bytes)))
        assertTrue(script.startsWith("window.kvistDrop && window.kvistDrop(["))
        val items = JSONArray(script.removePrefix("window.kvistDrop && window.kvistDrop(").removeSuffix(")"))
        assertEquals("a\"');x", items.getJSONObject(0).getString("name"))
        assertEquals("image/jpeg", items.getJSONObject(0).getString("type"))
        assertTrue(Base64.getDecoder().decode(items.getJSONObject(0).getString("data")).contentEquals(bytes))
    }

    @Test
    fun aStreamIsReadWholeOrNotAtAll() {
        val data = ByteArray(200_000) { (it % 251).toByte() }
        assertTrue(Photos.readCapped(data.inputStream(), 200_000)!!.contentEquals(data))
        assertNull(Photos.readCapped(data.inputStream(), 199_999))
    }

    @Test
    fun noMoreThanTheMessageTakes() {
        val many = List(12) { Photos.Dropped("p$it", "image/png", byteArrayOf(1)) }
        val items = JSONArray(Photos.dropScript(many).removePrefix("window.kvistDrop && window.kvistDrop(").removeSuffix(")"))
        assertEquals(Photos.MAX_COUNT, items.length())
    }
}
