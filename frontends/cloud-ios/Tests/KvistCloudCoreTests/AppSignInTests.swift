import XCTest
@testable import KvistCloudCore

final class AppSignInTests: XCTestCase {
    private let cloud = "https://cloud.kvist.site"

    func testANonceIsLongHexAndNeverRepeats() {
        let first = AppSignIn.makeNonce()
        XCTAssertEqual(first.count, 64)
        XCTAssertNotNil(first.range(of: "^[0-9a-f]{64}$", options: .regularExpression))
        XCTAssertNotEqual(first, AppSignIn.makeNonce())
    }

    func testTheStartAddressIsTheClouds() throws {
        let url = try XCTUnwrap(AppSignIn.startURL(cloudURL: cloud, nonce: "abc"))
        XCTAssertEqual(url.absoluteString, "https://cloud.kvist.site/auth/app/start?nonce=abc")
    }

    func testTheCodeIsReadOnlyFromTheAppsOwnAddress() {
        XCTAssertEqual(AppSignIn.code(from: URL(string: "kvistcloud://signed-in?code=A.B.C")!), "A.B.C")
        XCTAssertNil(AppSignIn.code(from: URL(string: "kvistcloud://other?code=A")!))
        XCTAssertNil(AppSignIn.code(from: URL(string: "https://evil.example/signed-in?code=A")!))
        XCTAssertNil(AppSignIn.code(from: URL(string: "kvistcloud://signed-in")!))
        XCTAssertNil(AppSignIn.code(from: URL(string: "kvistcloud://signed-in?code=")!))
    }

    func testTheExchangeCarriesTheCodeTheNonceAndWhereToLand() throws {
        let url = try XCTUnwrap(AppSignIn.exchangeURL(cloudURL: cloud, code: "A.B-C_d", nonce: "n1"))
        let items = Dictionary(uniqueKeysWithValues: (URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? [])
            .map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(url.path, "/auth/app/exchange")
        XCTAssertEqual(items, ["code": "A.B-C_d", "nonce": "n1", "next": "/m/"])
    }
}
