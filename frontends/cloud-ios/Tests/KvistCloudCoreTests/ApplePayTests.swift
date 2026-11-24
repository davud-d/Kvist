import XCTest
@testable import KvistCloudCore

final class ApplePayTests: XCTestCase {
    private func request(_ change: (inout [String: Any]) -> Void = { _ in }) -> [String: Any] {
        var object: [String: Any] = [
            "merchantIdentifier": "merchant.site.kvist.cloud", "countryCode": "US", "currencyCode": "USD",
            "supportedNetworks": ["amex", "discover", "masterCard", "visa"], "merchantCapabilities": ["3DS"],
            "summaryItems": [["label": "Pro, 30 days", "amount": "39.00"], ["label": "Kvist Cloud", "amount": "39.00"]],
        ]
        change(&object)
        return object
    }

    func testTheServersRequestIsReadAsItIs() throws {
        let parsed = try XCTUnwrap(ApplePayRequest.parse(request()))
        XCTAssertEqual(parsed.merchantIdentifier, "merchant.site.kvist.cloud")
        XCTAssertEqual(parsed.currencyCode, "USD")
        XCTAssertEqual(parsed.supportedNetworks, ["amex", "discover", "masterCard", "visa"])
        XCTAssertEqual(parsed.total, ApplePayItem(label: "Kvist Cloud", amount: "39.00"))
        XCTAssertEqual(parsed.summaryItems.count, 2)
    }

    func testARequestThatCannotBeAPaymentIsRefused() {
        XCTAssertNil(ApplePayRequest.parse(nil))
        XCTAssertNil(ApplePayRequest.parse(request { $0["merchantIdentifier"] = "" }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["merchantIdentifier"] = "site.kvist.cloud" }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["countryCode"] = "USA" }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["currencyCode"] = "US" }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["supportedNetworks"] = [String]() }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["summaryItems"] = [[String: Any]]() }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["summaryItems"] = [["label": "x", "amount": "free"]] }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["summaryItems"] = [["label": "x", "amount": "-5.00"]] }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["summaryItems"] = [["label": "x", "amount": "1.234"]] }))
        XCTAssertNil(ApplePayRequest.parse(request { $0["summaryItems"] = [["label": "", "amount": "1.00"]] }))
    }

    func testAmountsAreDecimalsWithAtMostTwoPlaces() {
        XCTAssertTrue(ApplePayRequest.isAmount("39"))
        XCTAssertTrue(ApplePayRequest.isAmount("39.5"))
        XCTAssertTrue(ApplePayRequest.isAmount("0.99"))
        XCTAssertFalse(ApplePayRequest.isAmount(""))
        XCTAssertFalse(ApplePayRequest.isAmount("1e3"))
        XCTAssertFalse(ApplePayRequest.isAmount("1,5"))
    }

    func testTheTokenIsHandedToTheServerInTheShapeItReads() throws {
        let data = Data(#"{"version":"EC_v1","data":"x"}"#.utf8)
        let token = WalletToken(paymentData: data, transactionIdentifier: "tx-1", network: "Visa", displayName: "Visa 4242")
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(token.json.utf8)) as? [String: String])
        // The server forwards paymentData to Stripe as text: it must be the token's own JSON, not a re-encoded object.
        XCTAssertEqual(object["paymentData"], #"{"version":"EC_v1","data":"x"}"#)
        XCTAssertEqual(object["transactionIdentifier"], "tx-1")
        XCTAssertEqual(object["network"], "Visa")
        XCTAssertEqual(object["displayName"], "Visa 4242")
        XCTAssertEqual(Set(object.keys), ["paymentData", "transactionIdentifier", "network", "displayName"])
    }
}
