import XCTest
@testable import KvistCore

final class BridgeTests: XCTestCase {
    private func object(_ text: String) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
    }

    func testRequestsFromTheConnectScreenAreParsed() {
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"saved"}"#), .saved)
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"scan"}"#), .scan)
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"forget"}"#), .forget)
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"pair","id":"abc","name":"desk","host":"10.0.0.2","port":9700,"auto":true}"#),
                       .pair(PairRequest(host: "10.0.0.2", port: 9700, id: "abc", name: "desk", auto: true)))
        XCTAssertNil(BridgeRequest.parse(#"{"op":"format"}"#))
        XCTAssertNil(BridgeRequest.parse("nonsense"))
    }

    func testAPairRequestTypedByHandHasNoIdAndTheDefaultPort() {
        // connect.js sends `id: ''` and a port of 0 or none for an address typed by hand.
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"pair","id":"","name":"10.0.0.2","host":"10.0.0.2","port":9671,"auto":false}"#),
                       .pair(PairRequest(host: "10.0.0.2")))
        XCTAssertEqual(BridgeRequest.parse(#"{"op":"pair","host":"10.0.0.2"}"#), .pair(PairRequest(host: "10.0.0.2")))
    }

    func testRepliesHaveTheShapeConnectJsReads() throws {
        let saved = Saved(id: "abc", name: "desk", host: "10.0.0.2", port: 9671, token: "secret")
        let known = try object(BridgeReply.saved(saved).json)
        XCTAssertEqual(known["type"] as? String, "saved")
        XCTAssertEqual((known["saved"] as? [String: Any])?["host"] as? String, "10.0.0.2")
        XCTAssertNil((known["saved"] as? [String: Any])?["token"], "the page never sees the token")
        XCTAssertTrue(try object(BridgeReply.saved(nil).json)["saved"] is NSNull)

        let computer = Computer(id: "abc", name: "desk", host: "10.0.0.2", port: 9671, os: "linux", osName: "Arch", user: "ann")
        let found = try object(BridgeReply.found([Found(computer: computer, saved: true)]).json)
        let first = try XCTUnwrap((found["computers"] as? [[String: Any]])?.first)
        XCTAssertEqual(found["type"] as? String, "found")
        XCTAssertEqual(first["os_name"] as? String, "Arch")
        XCTAssertEqual(first["saved"] as? Bool, true)
        XCTAssertEqual(first["port"] as? Int, 9671)

        XCTAssertEqual(try object(BridgeReply.paired(saved).json) as NSDictionary, ["type": "paired"] as NSDictionary)
        let failed = try object(BridgeReply.pairFailed(reason: "unreachable", detail: "h:1 refused").json)
        XCTAssertEqual(failed["type"] as? String, "pair-failed")
        XCTAssertEqual(failed["detail"] as? String, "h:1 refused")
        XCTAssertNil(try object(BridgeReply.pairFailed(reason: "denied", detail: nil).json)["detail"])
    }

    func testTheBridgeAnswersFromTheStoreAndTheConnector() {
        let store = MemoryStore(saved: Saved(id: "abc", name: "desk", host: "10.0.0.2", port: 9671, token: "t"))
        let transport = FakeTransport(["GET /": [.response(200, "")]])
        let connector = Connector(store: store, pairing: Pairing(transport: transport), deviceName: "iPad", subnets: { [] }, log: { _ in }, sleep: { _ in })
        let bridge = PageBridge(connector: connector, store: store)

        XCTAssertEqual(bridge.respond(to: .saved), .saved(store.saved))
        XCTAssertEqual(bridge.respond(to: .pair(PairRequest(host: "10.0.0.2", id: "abc", name: "desk", auto: true))),
                       .paired(Saved(id: "abc", name: "desk", host: "10.0.0.2", port: 9671, token: "t")))
        XCTAssertEqual(bridge.respond(to: .pair(PairRequest(host: "10.0.0.3", id: "zzz", auto: true))), .pairFailed(reason: "forgotten", detail: nil))
        XCTAssertNil(bridge.respond(to: .forget))
        XCTAssertEqual(store.forgotten, 1)
        XCTAssertNil(store.saved)
    }
}
