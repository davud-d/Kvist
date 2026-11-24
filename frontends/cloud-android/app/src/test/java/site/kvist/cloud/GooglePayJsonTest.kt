package site.kvist.cloud

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GooglePayJsonTest {
    @Test
    fun theReadinessRequestAsksForCardsOnlyWithoutPriceOrGateway() {
        val request = JSONObject(GooglePayJson.readyRequest())
        assertEquals(2, request.getInt("apiVersion"))
        assertEquals(0, request.getInt("apiVersionMinor"))
        val method = request.getJSONArray("allowedPaymentMethods").getJSONObject(0)
        assertEquals("CARD", method.getString("type"))
        assertFalse(method.has("tokenizationSpecification"))
        assertFalse(request.has("transactionInfo"))
        val parameters = method.getJSONObject("parameters")
        assertEquals(setOf("PAN_ONLY", "CRYPTOGRAM_3DS"), (0 until 2).map { parameters.getJSONArray("allowedAuthMethods").getString(it) }.toSet())
        assertTrue(parameters.getJSONArray("allowedCardNetworks").length() >= 4)
    }

    @Test
    fun theTokenIsTheTextTheWalletPutInTokenizationData() {
        val data = """{"apiVersion":2,"paymentMethodData":{"type":"CARD","tokenizationData":{"type":"PAYMENT_GATEWAY","token":"{\"id\":\"tok_123\"}"}}}"""
        assertEquals("""{"id":"tok_123"}""", GooglePayJson.tokenOf(data))
        assertNull(GooglePayJson.tokenOf("{}"))
        assertNull(GooglePayJson.tokenOf("garbage"))
        assertNull(GooglePayJson.tokenOf("""{"paymentMethodData":{"tokenizationData":{"token":""}}}"""))
    }

    @Test
    fun aRequestWithoutAPriceOrWithTwoCardMethodsIsNotOpened() {
        val good = JSONObject("""{"allowedPaymentMethods":[{"type":"CARD"}],"transactionInfo":{"totalPrice":"15.00"}}""")
        assertTrue(GooglePayJson.validRequest(good))
        assertFalse(GooglePayJson.validRequest(null))
        assertFalse(GooglePayJson.validRequest(JSONObject("""{"allowedPaymentMethods":[{"type":"CARD"}]}""")))
        assertFalse(GooglePayJson.validRequest(JSONObject("""{"allowedPaymentMethods":[],"transactionInfo":{"totalPrice":"1.00"}}""")))
        assertFalse(GooglePayJson.validRequest(JSONObject("""{"allowedPaymentMethods":[{},{}],"transactionInfo":{"totalPrice":"1.00"}}""")))
    }

    @Test
    fun onlyTheWordProductionIsProduction() {
        assertTrue(GooglePayJson.production("PRODUCTION"))
        assertFalse(GooglePayJson.production("production"))
        assertFalse(GooglePayJson.production("TEST"))
        assertFalse(GooglePayJson.production(null))
        assertFalse(GooglePayJson.production(""))
    }
}
