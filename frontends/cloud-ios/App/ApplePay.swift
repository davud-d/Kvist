import KvistCloudCore
import PassKit

/// The Apple Pay sheet. The request is the server's (price, merchant, networks); this opens the operating system's
/// sheet with it and hands back the wallet's token or "cancelled". It decides nothing and keeps nothing.
///
/// The sheet stays open, showing its spinner, until the page says whether the server charged the token (`finish`):
/// the person must not see "Done" for a payment that is then refused.
final class ApplePay: NSObject, PKPaymentAuthorizationControllerDelegate {
    private static let networks: [PKPaymentNetwork] = [.amex, .discover, .masterCard, .visa]
    /// Apple asks for an answer within about half a minute of the authorization; past that the sheet fails by itself.
    private static let answerSeconds: TimeInterval = 25

    private var controller: PKPaymentAuthorizationController?
    private var reply: (([String: Any]) -> Void)?
    private var authorized: ((PKPaymentAuthorizationResult) -> Void)?
    private var timeout: DispatchWorkItem?

    /// Can this phone pay with a card in Wallet? (False on a device with no card, and without the entitlement.)
    static func isReady() -> Bool {
        PKPaymentAuthorizationController.canMakePayments(usingNetworks: networks)
    }

    func pay(_ request: ApplePayRequest, reply: @escaping ([String: Any]) -> Void) {
        guard controller == nil else {
            reply(Bridge.failed("wallet_unavailable"))
            return
        }
        let payment = PKPaymentRequest()
        payment.merchantIdentifier = request.merchantIdentifier
        payment.countryCode = request.countryCode
        payment.currencyCode = request.currencyCode
        payment.supportedNetworks = request.supportedNetworks.map { PKPaymentNetwork(rawValue: $0) }
        payment.merchantCapabilities = .threeDSecure
        payment.paymentSummaryItems = request.summaryItems.map {
            PKPaymentSummaryItem(label: $0.label, amount: NSDecimalNumber(string: $0.amount, locale: Locale(identifier: "en_US_POSIX")))
        }
        let sheet = PKPaymentAuthorizationController(paymentRequest: payment)
        sheet.delegate = self
        controller = sheet
        self.reply = reply
        sheet.present { [weak self] presented in
            // Not presented: no card, no entitlement, or a request Apple refuses. Nothing was shown, nothing is owed.
            if !presented { self?.end(Bridge.failed("wallet_unavailable")) }
        }
    }

    /// The page's word on the charge: true confirms the sheet with its checkmark, false shows it failed.
    func finish(ok: Bool) {
        timeout?.cancel()
        guard let authorized else { return }
        self.authorized = nil
        authorized(PKPaymentAuthorizationResult(status: ok ? .success : .failure, errors: nil))
    }

    // MARK: PKPaymentAuthorizationControllerDelegate

    func paymentAuthorizationController(
        _ controller: PKPaymentAuthorizationController,
        didAuthorizePayment payment: PKPayment,
        handler completion: @escaping (PKPaymentAuthorizationResult) -> Void
    ) {
        let token = WalletToken(
            paymentData: payment.token.paymentData,
            transactionIdentifier: payment.token.transactionIdentifier,
            network: payment.token.paymentMethod.network?.rawValue ?? "",
            displayName: payment.token.paymentMethod.displayName ?? ""
        )
        authorized = completion
        let work = DispatchWorkItem { [weak self] in self?.finish(ok: false) }
        timeout = work
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.answerSeconds, execute: work)
        // The token goes to the page, which hands it to the server; the sheet waits for `finish`.
        let answer = Bridge.token(token.json)
        let send = reply
        reply = nil
        send?(answer)
    }

    func paymentAuthorizationControllerDidFinish(_ controller: PKPaymentAuthorizationController) {
        controller.dismiss { [weak self] in
            // A sheet closed without an authorization is the person changing their mind.
            self?.end(Bridge.cancelled())
        }
    }

    private func end(_ result: [String: Any]) {
        timeout?.cancel()
        controller = nil
        authorized = nil
        let send = reply
        reply = nil
        send?(result)
    }
}
