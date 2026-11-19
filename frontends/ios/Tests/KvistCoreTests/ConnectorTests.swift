import XCTest
@testable import KvistCore

final class ConnectorTests: XCTestCase {
    private let known = Saved(id: "abc", name: "desk", host: "192.168.1.5", port: 9671, token: "old")

    private func connector(_ store: MemoryStore, _ transport: FakeTransport, polls: Int = 75) -> Connector {
        Connector(store: store, pairing: Pairing(transport: transport), deviceName: "iPad", subnets: { [] }, log: { _ in },
                  answerPolls: polls, sleep: { _ in })
    }

    private func request(_ host: String = "192.168.1.9", id: String = "abc", auto: Bool = false) -> PairRequest {
        PairRequest(host: host, port: 9671, id: id, name: "desk", auto: auto)
    }

    func testAKnownComputerOpensAtOnceAtItsNewAddress() {
        let transport = FakeTransport(["GET /": [.response(200, "")]])
        let outcome = connector(MemoryStore(saved: known), transport).pair(request())
        XCTAssertEqual(outcome, .opened(Saved(id: "abc", name: "desk", host: "192.168.1.9", port: 9671, token: "old")))
        XCTAssertEqual(transport.calls.map(\.url), ["http://192.168.1.9:9671/"], "no window is made to appear on the computer")
    }

    func testTheAppStartingUpNeverAsksAnUnknownComputer() {
        let transport = FakeTransport()
        XCTAssertEqual(connector(MemoryStore(), transport).pair(request(auto: true)), .failed(reason: "forgotten"))
        XCTAssertEqual(connector(MemoryStore(saved: known), transport).pair(request(id: "other", auto: true)), .failed(reason: "forgotten"))
        XCTAssertTrue(transport.calls.isEmpty)
    }

    func testAComputerThatForgotThePhoneIsNotAskedAgainWhenTheAppStartsByItself() {
        let transport = FakeTransport(["GET /": [.response(401, "")]])
        XCTAssertEqual(connector(MemoryStore(saved: known), transport).pair(request(auto: true)), .failed(reason: "forgotten"))
        XCTAssertEqual(transport.calls.count, 1)
    }

    func testATapOnAComputerThatForgotThePhoneAsksItsOwner() {
        let transport = FakeTransport([
            "GET /": [.response(401, "")],
            "POST /pair/request": [.response(200, #"{"request_id":"r1"}"#)],
            "GET /pair/status": [.response(200, #"{"state":"approved","token":"fresh"}"#)],
        ])
        let outcome = connector(MemoryStore(saved: known), transport).pair(request())
        XCTAssertEqual(outcome, .opened(Saved(id: "abc", name: "desk", host: "192.168.1.9", port: 9671, token: "fresh")))
    }

    func testAnUnreachableKnownComputerSaysWhy() {
        let transport = FakeTransport(["GET /": [.failure("URLError: timed out")]])
        XCTAssertEqual(connector(MemoryStore(saved: known), transport).pair(request()),
                       .failed(reason: "unreachable", detail: "192.168.1.9:9671 URLError: timed out"))
    }

    func testTheOwnerAllowsAfterAFewPolls() {
        let transport = FakeTransport([
            "POST /pair/request": [.response(200, #"{"request_id":"r1"}"#)],
            "GET /pair/status": [.response(200, #"{"state":"pending"}"#), .response(200, #"{"state":"pending"}"#), .response(200, #"{"state":"approved","token":"t"}"#)],
        ])
        let outcome = connector(MemoryStore(), transport).pair(request(id: "xyz"))
        XCTAssertEqual(outcome, .opened(Saved(id: "xyz", name: "desk", host: "192.168.1.9", port: 9671, token: "t")))
        XCTAssertEqual(transport.calls.filter { $0.url.contains("/pair/status") }.count, 3)
    }

    func testTheDeviceIdAndNameGoToTheComputer() throws {
        let transport = FakeTransport(["POST /pair/request": [.response(429, #"{"error":"busy"}"#)]])
        _ = connector(MemoryStore(), transport).pair(request())
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(transport.calls[0].body.utf8)) as? [String: String])
        XCTAssertEqual(body, ["device_id": "device-1", "name": "iPad"])
    }

    func testEveryWayTheOwnerCanSayNo() {
        func outcome(_ reply: String) -> PairOutcome {
            let transport = FakeTransport(["POST /pair/request": [.response(200, #"{"request_id":"r"}"#)], "GET /pair/status": [.response(200, reply)]])
            return connector(MemoryStore(), transport).pair(request())
        }
        XCTAssertEqual(outcome(#"{"state":"denied"}"#), .failed(reason: "denied"))
        XCTAssertEqual(outcome(#"{"state":"unavailable"}"#), .failed(reason: "unavailable"))
        XCTAssertEqual(outcome(#"{"state":"expired"}"#), .failed(reason: "expired"))
    }

    func testNobodyAnsweringEndsAsExpired() {
        let transport = FakeTransport(["POST /pair/request": [.response(200, #"{"request_id":"r"}"#)], "GET /pair/status": [.response(200, #"{"state":"pending"}"#)]])
        XCTAssertEqual(connector(MemoryStore(), transport, polls: 3).pair(request()), .failed(reason: "expired"))
        XCTAssertEqual(transport.calls.filter { $0.url.contains("/pair/status") }.count, 3)
    }

    func testRefusalsAreForwardedAsTheyAre() {
        for reason in ["busy", "cooldown"] {
            let transport = FakeTransport(["POST /pair/request": [.response(429, #"{"error":"\#(reason)"}"#)]])
            XCTAssertEqual(connector(MemoryStore(), transport).pair(request()), .failed(reason: reason))
        }
    }

    func testAnUnreachableComputerOnAFirstAskSaysWhy() {
        let transport = FakeTransport(["POST /pair/request": [.failure("refused")]])
        XCTAssertEqual(connector(MemoryStore(), transport).pair(request()), .failed(reason: "unreachable", detail: "192.168.1.9:9671 refused"))
    }

    func testABlankOrImpossibleAddressIsUnreachable() {
        let transport = FakeTransport()
        XCTAssertEqual(connector(MemoryStore(), transport).pair(PairRequest(host: "  ")), .failed(reason: "unreachable"))
        XCTAssertEqual(connector(MemoryStore(), transport).pair(PairRequest(host: "h", port: 0)), .failed(reason: "unreachable"))
        XCTAssertEqual(connector(MemoryStore(), transport).pair(PairRequest(host: "h", port: 70000)), .failed(reason: "unreachable"))
        XCTAssertTrue(transport.calls.isEmpty)
    }

    func testTheAddressTypedByHandIsTrimmedAndNamesItself() {
        let typed = PairRequest(host: " 192.168.1.20 ")
        XCTAssertEqual(typed.host, "192.168.1.20")
        XCTAssertEqual(typed.id, "192.168.1.20")
        XCTAssertEqual(typed.name, "192.168.1.20")
        XCTAssertEqual(typed.port, 9671)
    }
}
