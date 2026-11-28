import XCTest
@testable import KvistCloudCore

final class BridgeTests: XCTestCase {
    func testACallCarriesItsIdItsNameAndItsArguments() throws {
        let body: [String: Any] = ["id": "c7", "name": "pay", "json": #"{"wallet":"apple_pay","request":{"countryCode":"US"}}"#]
        let call = try XCTUnwrap(Bridge.parse(body))
        XCTAssertEqual(call.id, "c7")
        XCTAssertEqual(call.name, "pay")
        XCTAssertEqual(call.args["wallet"] as? String, "apple_pay")
        XCTAssertEqual((call.args["request"] as? [String: Any])?["countryCode"] as? String, "US")
    }

    func testACallWithoutArgumentsHasEmptyOnes() throws {
        XCTAssertEqual(try XCTUnwrap(Bridge.parse(["id": "c1", "name": "wallets", "json": "{}"])).args.count, 0)
        XCTAssertEqual(try XCTUnwrap(Bridge.parse(["id": "c1", "name": "wallets"])).args.count, 0)
        XCTAssertEqual(try XCTUnwrap(Bridge.parse(["id": "c1", "name": "wallets", "json": ""])).args.count, 0)
    }

    func testNonsenseIsIgnoredNotAnError() {
        XCTAssertNil(Bridge.parse("text"))
        XCTAssertNil(Bridge.parse(["name": "pay"]))
        XCTAssertNil(Bridge.parse(["id": "c1"]))
        XCTAssertNil(Bridge.parse(["id": "", "name": "pay"]))
        XCTAssertNil(Bridge.parse(["id": "c1", "name": "pay", "json": "not json"]))
        XCTAssertNil(Bridge.parse(["id": "c1", "name": "pay", "json": "[1,2]"]))
    }

    func testRepliesAreTheShapesThePageReads() {
        XCTAssertEqual(Bridge.token("t")["token"] as? String, "t")
        XCTAssertEqual(Bridge.cancelled()["cancelled"] as? Bool, true)
        XCTAssertEqual(Bridge.failed("wallet_error")["error"] as? String, "wallet_error")
    }

    func testTheStartScriptSpeaksTheWordsThePageExpects() {
        // cloud/mobile/js/native.js calls KvistHost.call(id, name, json) and is answered through __kvistReply(id, payload).
        XCTAssertTrue(Bridge.startScript(store: false).contains("window.KvistHost"))
        XCTAssertTrue(Bridge.startScript(store: false).contains(#"platform: "ios""#))
        XCTAssertTrue(Bridge.startScript(store: false).contains("window.__kvistReply(id,"))
        XCTAssertTrue(Bridge.startScript(store: false).contains("handlers.\(Bridge.handlerName).postMessage"))
        XCTAssertTrue(Bridge.startScript(store: false).contains("store: false,"))
        XCTAssertTrue(Bridge.startScript(store: true).contains("store: true,"))
    }
}
