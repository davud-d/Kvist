package site.kvist.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DiscoveryTest {
    private fun parse(text: String, sender: String = "192.168.1.23") = Discovery.parseReply(text.toByteArray(), text.toByteArray().size, sender)

    @Test
    fun readsAKvistReply() {
        val computer = parse("""{"service":"kvist-web","version":2,"id":"abc","name":"studio","os":"macos","os_name":"macOS 15.1","user":"ana","port":9671}""")
        assertEquals(Computer("abc", "studio", "192.168.1.23", 9671, "macos", "macOS 15.1", "ana"), computer)
    }

    @Test
    fun aMissingNameAndIdFallBackToTheAddress() {
        val computer = parse("""{"service":"kvist-web","port":9671}""")
        assertEquals("192.168.1.23", computer?.name)
        assertEquals("192.168.1.23", computer?.id)
    }

    @Test
    fun ignoresWhatIsNotKvist() {
        assertNull(parse("""{"service":"printer","port":9671}"""))
        assertNull(parse("not json"))
        assertNull(parse("""{"service":"kvist-web","port":70000}"""))
        assertNull(parse("""{"service":"kvist-web"}"""))
    }

    @Test
    fun broadcastAddressOfASubnet() {
        fun of(ip: String, prefix: Int) = Discovery.broadcastOf(java.net.InetAddress.getByName(ip).address, prefix).hostAddress
        assertEquals("192.168.1.255", of("192.168.1.23", 24))
        assertEquals("10.0.255.255", of("10.0.4.7", 16))
        assertEquals("192.168.1.23", of("192.168.1.23", 32))
        assertEquals("255.255.255.255", of("192.168.1.23", 0))
    }
}
