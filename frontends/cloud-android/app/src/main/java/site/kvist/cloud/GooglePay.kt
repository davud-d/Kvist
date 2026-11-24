package site.kvist.cloud

import androidx.activity.ComponentActivity
import com.google.android.gms.common.api.CommonStatusCodes
import com.google.android.gms.wallet.IsReadyToPayRequest
import com.google.android.gms.wallet.PaymentData
import com.google.android.gms.wallet.PaymentDataRequest
import com.google.android.gms.wallet.PaymentsClient
import com.google.android.gms.wallet.Wallet
import com.google.android.gms.wallet.WalletConstants
import com.google.android.gms.wallet.contract.TaskResultContracts.GetPaymentDataResult
import org.json.JSONObject

/**
 * The Google Pay sheet. The request is the server's (price, merchant, gateway); this opens the operating system's sheet
 * with it and hands back the wallet's token or "cancelled". It decides nothing and keeps nothing.
 */
class GooglePay(private val activity: ComponentActivity) {
    private var waiting: ((JSONObject) -> Unit)? = null

    // Registered while the activity is created, as the Activity Result API requires.
    private val launcher = activity.registerForActivityResult(GetPaymentDataResult()) { result ->
        val finish = waiting ?: return@registerForActivityResult
        waiting = null
        when (result.status.statusCode) {
            CommonStatusCodes.SUCCESS -> {
                val token = result.result?.let { data: PaymentData -> GooglePayJson.tokenOf(data.toJson()) }
                finish(if (token != null) Bridge.token(token) else Bridge.failed("wallet_error"))
            }
            CommonStatusCodes.CANCELED -> finish(Bridge.cancelled())
            else -> finish(Bridge.failed("wallet_error"))
        }
    }

    private val clients = HashMap<Boolean, PaymentsClient>()

    private fun client(production: Boolean): PaymentsClient = clients.getOrPut(production) {
        val environment = if (production) WalletConstants.ENVIRONMENT_PRODUCTION else WalletConstants.ENVIRONMENT_TEST
        Wallet.getPaymentsClient(activity, Wallet.WalletOptions.Builder().setEnvironment(environment).build())
    }

    /** Can this phone pay with a Google Pay card? Asked in the production environment's terms only when it matters. */
    fun isReady(answer: (Boolean) -> Unit) {
        // Whether a wallet exists does not depend on the environment; TEST asks without needing a merchant.
        val request = IsReadyToPayRequest.fromJson(GooglePayJson.readyRequest())
        client(false).isReadyToPay(request).addOnCompleteListener { task -> answer(task.isSuccessful && task.result == true) }
    }

    /** {wallet: "google_pay", request: <PaymentDataRequest JSON>, environment: "TEST" | "PRODUCTION"} */
    fun pay(args: JSONObject, answer: (JSONObject) -> Unit) {
        val request = args.optJSONObject("request")
        if (request == null || !GooglePayJson.validRequest(request) || waiting != null) {
            answer(Bridge.failed("wallet_unavailable"))
            return
        }
        waiting = answer
        val task = client(GooglePayJson.production(args.optString("environment")))
            .loadPaymentData(PaymentDataRequest.fromJson(request.toString()))
        launcher.launch(task)
    }
}
