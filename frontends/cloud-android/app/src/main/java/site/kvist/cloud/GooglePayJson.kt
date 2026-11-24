package site.kvist.cloud

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** The JSON the Google Pay API takes and gives, built and read in one place. */
object GooglePayJson {
    private val NETWORKS = listOf("AMEX", "DISCOVER", "JCB", "MASTERCARD", "VISA")

    /** Is this phone able to pay with a card from Google Pay? Only the card method, as the checklist asks. */
    fun readyRequest(): String {
        val card = JSONObject()
            .put("type", "CARD")
            .put(
                "parameters",
                JSONObject()
                    .put("allowedAuthMethods", JSONArray(listOf("PAN_ONLY", "CRYPTOGRAM_3DS")))
                    .put("allowedCardNetworks", JSONArray(NETWORKS)),
            )
        return JSONObject()
            .put("apiVersion", 2)
            .put("apiVersionMinor", 0)
            .put("allowedPaymentMethods", JSONArray().put(card))
            .toString()
    }

    /**
     * The wallet's token out of a PaymentData result: paymentMethodData.tokenizationData.token. It is handed to the
     * server as the text it is; the app never looks inside it and never keeps it.
     */
    fun tokenOf(paymentDataJson: String): String? = try {
        JSONObject(paymentDataJson).getJSONObject("paymentMethodData").getJSONObject("tokenizationData")
            .getString("token").takeIf { it.isNotEmpty() }
    } catch (error: JSONException) {
        null
    }

    /** The server's request is the one thing the sheet is opened with; it must at least be a card request with a price. */
    fun validRequest(request: JSONObject?): Boolean {
        if (request == null) return false
        val price = request.optJSONObject("transactionInfo")?.optString("totalPrice").orEmpty()
        return request.optJSONArray("allowedPaymentMethods")?.length() == 1 && price.isNotEmpty()
    }

    /** "TEST" unless the server says "PRODUCTION": a typo must not charge a real card. */
    fun production(environment: String?): Boolean = environment == "PRODUCTION"
}
