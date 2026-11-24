import Foundation

public struct ApplePayItem: Equatable {
    public let label: String
    public let amount: String
}

/// What the server asks the sheet to show (cloud/backend/kvist_cloud/wallet.py, apple_request). The app builds a
/// PKPaymentRequest from it and decides nothing: the price, the merchant and the networks are the server's.
public struct ApplePayRequest: Equatable {
    public let merchantIdentifier: String
    public let countryCode: String
    public let currencyCode: String
    public let supportedNetworks: [String]
    public let merchantCapabilities: [String]
    public let summaryItems: [ApplePayItem]

    /// The total: Apple's rule is that the last summary item is the sum of the others, named after the seller.
    public var total: ApplePayItem { summaryItems[summaryItems.count - 1] }

    /// nil when the request cannot be a payment: no merchant, a bad country or currency, no items, an amount that is
    /// not a decimal number with at most two places.
    public static func parse(_ object: [String: Any]?) -> ApplePayRequest? {
        guard let object,
              let merchant = object["merchantIdentifier"] as? String, merchant.hasPrefix("merchant."),
              let country = object["countryCode"] as? String, country.count == 2,
              let currency = object["currencyCode"] as? String, currency.count == 3,
              let networks = object["supportedNetworks"] as? [String], !networks.isEmpty,
              let capabilities = object["merchantCapabilities"] as? [String],
              let rows = object["summaryItems"] as? [[String: Any]], !rows.isEmpty else { return nil }
        var items: [ApplePayItem] = []
        for row in rows {
            guard let label = row["label"] as? String, !label.isEmpty,
                  let amount = row["amount"] as? String, isAmount(amount) else { return nil }
            items.append(ApplePayItem(label: label, amount: amount))
        }
        return ApplePayRequest(merchantIdentifier: merchant, countryCode: country.uppercased(),
                               currencyCode: currency.uppercased(), supportedNetworks: networks,
                               merchantCapabilities: capabilities, summaryItems: items)
    }

    static func isAmount(_ text: String) -> Bool {
        text.range(of: #"^[0-9]+(\.[0-9]{1,2})?$"#, options: .regularExpression) != nil
    }
}

/// What the page sends the server after the sheet: the PKPaymentToken as the server's /api/wallet/pay reads it.
public struct WalletToken {
    public let paymentData: Data
    public let transactionIdentifier: String
    public let network: String
    public let displayName: String

    public init(paymentData: Data, transactionIdentifier: String, network: String, displayName: String) {
        self.paymentData = paymentData
        self.transactionIdentifier = transactionIdentifier
        self.network = network
        self.displayName = displayName
    }

    /// {"paymentData": "<the token's own JSON, as text>", "transactionIdentifier", "network", "displayName"}.
    public var json: String {
        let object: [String: String] = [
            "paymentData": String(decoding: paymentData, as: UTF8.self),
            "transactionIdentifier": transactionIdentifier,
            "network": network,
            "displayName": displayName,
        ]
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self)
    }
}
