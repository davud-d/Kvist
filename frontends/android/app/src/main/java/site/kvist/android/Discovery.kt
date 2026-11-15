package site.kvist.android

import android.util.Log
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException

/** What a computer says about itself: enough for a person to pick the right one from a list. */
data class Computer(
    val id: String,
    val name: String,
    val host: String,
    val port: Int,
    val os: String,
    val osName: String,
    val user: String,
)

/**
 * Finds computers running `kvist-web --lan` on the same router. The phone sends a short probe to the
 * network's broadcast address and every Kvist answers with its name and port. The address of a computer
 * is the sender of its answer, so a computer with several adapters is reached by the one the phone can see.
 */
object Discovery {
    const val BEACON_PORT = 9672
    private const val TAG = "KvistDiscovery"
    private const val RESEND_AFTER_MS = 600L
    private const val RECEIVE_TIMEOUT_MS = 150
    private val PROBE = "KVIST-DISCOVER/1".toByteArray(Charsets.US_ASCII)

    fun parseReply(data: ByteArray, length: Int, sender: String): Computer? {
        val json = try {
            JSONObject(String(data, 0, length, Charsets.UTF_8))
        } catch (error: JSONException) {
            return null
        }
        if (json.optString("service") != "kvist-web") return null
        val port = json.optInt("port", -1)
        if (port !in 1..65535) return null
        return Computer(
            id = json.optString("id").ifBlank { sender },
            name = json.optString("name").ifBlank { sender },
            host = sender,
            port = port,
            os = json.optString("os"),
            osName = json.optString("os_name"),
            user = json.optString("user"),
        )
    }

    /** The broadcast address of the subnet an IPv4 address lives in. */
    fun broadcastOf(address: ByteArray, prefixLength: Int): InetAddress {
        require(address.size == 4) { "IPv4 only" }
        require(prefixLength in 0..32) { "bad prefix length" }
        val host = if (prefixLength == 32) 0 else -1 ushr prefixLength
        val bytes = ByteArray(4) { index -> (address[index].toInt() or ((host ushr (24 - 8 * index)) and 0xFF)).toByte() }
        return InetAddress.getByAddress(bytes)
    }

    /** Blocking: sends the probe to every target, then listens for [windowMs]. Call it off the main thread. */
    fun scan(targets: List<InetAddress>, windowMs: Long): List<Computer> {
        val found = LinkedHashMap<String, Computer>()
        DatagramSocket().use { socket ->
            socket.broadcast = true
            socket.soTimeout = RECEIVE_TIMEOUT_MS
            val started = System.nanoTime()
            fun elapsedMs() = (System.nanoTime() - started) / 1_000_000
            sendProbes(socket, targets)
            var resent = false
            val buffer = ByteArray(512)
            while (elapsedMs() < windowMs) {
                // UDP drops packets, and a phone's radio may be asleep for the first one.
                if (!resent && elapsedMs() > RESEND_AFTER_MS) {
                    sendProbes(socket, targets)
                    resent = true
                }
                val packet = DatagramPacket(buffer, buffer.size)
                try {
                    socket.receive(packet)
                } catch (error: SocketTimeoutException) {
                    continue
                }
                val sender = packet.address.hostAddress ?: continue
                parseReply(packet.data, packet.length, sender)?.let { found[it.id] = it }
            }
        }
        return found.values.toList()
    }

    private fun sendProbes(socket: DatagramSocket, targets: List<InetAddress>) {
        for (target in targets) {
            try {
                socket.send(DatagramPacket(PROBE, PROBE.size, target, BEACON_PORT))
            } catch (error: IOException) {
                Log.w(TAG, "probe to $target failed: $error")
            }
        }
    }
}
