package site.kvist.cloud

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BridgeTest {
    @Test
    fun aCallCarriesItsIdItsNameAndItsArguments() {
        val call = Bridge.parse("""{"id":"c7","name":"pay","json":"{\"wallet\":\"google_pay\",\"environment\":\"TEST\"}"}""")
        assertNotNull(call)
        assertEquals("c7", call!!.id)
        assertEquals("pay", call.name)
        assertEquals("google_pay", call.args.getString("wallet"))
    }

    @Test
    fun aCallWithoutArgumentsHasEmptyOnes() {
        assertEquals(0, Bridge.parse("""{"id":"c1","name":"wallets","json":"{}"}""")!!.args.length())
        assertEquals(0, Bridge.parse("""{"id":"c1","name":"wallets"}""")!!.args.length())
        assertEquals(0, Bridge.parse("""{"id":"c1","name":"wallets","json":""}""")!!.args.length())
    }

    @Test
    fun nonsenseIsIgnoredNotAnError() {
        assertNull(Bridge.parse(null))
        assertNull(Bridge.parse("not json"))
        assertNull(Bridge.parse("""{"name":"pay"}"""))
        assertNull(Bridge.parse("""{"id":"c1"}"""))
        assertNull(Bridge.parse("""{"id":"c1","name":"pay","json":"not json"}"""))
    }

    @Test
    fun theAnswerNamesTheCallItAnswers() {
        val reply = JSONObject(Bridge.reply("c7", Bridge.token("tok_x")))
        assertEquals("c7", reply.getString("id"))
        assertEquals("tok_x", reply.getJSONObject("payload").getString("token"))
        assertTrue(JSONObject(Bridge.reply("c8", null)).isNull("payload"))
        assertTrue(JSONObject(Bridge.reply("c9", Bridge.cancelled())).getJSONObject("payload").getBoolean("cancelled"))
        assertEquals("wallet_error", JSONObject(Bridge.reply("c9", Bridge.failed("wallet_error"))).getJSONObject("payload").getString("error"))
    }

    @Test
    fun theStartScriptSpeaksTheWordsThePageExpects() {
        // cloud/mobile/js/native.js calls KvistHost.call(id, name, json) and is answered through __kvistReply(id, payload).
        assertTrue(Bridge.startScript(false).contains("window.KvistHost"))
        assertTrue(Bridge.startScript(false).contains("""platform: "android""""))
        assertTrue(Bridge.startScript(false).contains("window.__kvistReply(reply.id, reply.payload)"))
        assertTrue(Bridge.startScript(false).contains("window.${Bridge.CHANNEL}"))
        assertTrue(Bridge.startScript(false).contains("store: false,"))
        assertTrue(Bridge.startScript(true).contains("store: true,"))
    }
}
