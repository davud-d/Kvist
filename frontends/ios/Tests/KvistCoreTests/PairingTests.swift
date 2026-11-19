import XCTest
@testable import KvistCore

final class PairingTests: XCTestCase {
    func testTheAuthorizationHeaderIsBasicWithTheFixedUser() {
        XCTAssertEqual(Pairing.authHeader(token: "abc"), "Basic a3Zpc3Q6YWJj")
    }

    func testAnswersAreParsed() {
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"approved","token":"t0k"}"#), .allowed(token: "t0k"))
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"approved"}"#), .expired)
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"approved","token":""}"#), .expired)
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"pending"}"#), .pending)
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"denied"}"#), .denied)
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"unavailable"}"#), .unavailable)
        XCTAssertEqual(Pairing.parseAnswer(#"{"state":"whatever"}"#), .expired)
        XCTAssertEqual(Pairing.parseAnswer("<html>"), .expired)
    }

    func testAskSendsThePhoneAndReturnsTheRequestId() throws {
        let transport = FakeTransport(["POST /pair/request": [.response(200, #"{"request_id":"r1"}"#)]])
        XCTAssertEqual(Pairing(transport: transport).ask(host: "192.168.1.5", port: 9671, deviceId: "dev", deviceName: "iPad"), .id("r1"))
        let call = try XCTUnwrap(transport.calls.first)
        XCTAssertEqual(call.url, "http://192.168.1.5:9671/pair/request")
        XCTAssertEqual(call.headers["Content-Type"], "application/json")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(call.body.utf8)) as? [String: String])
        XCTAssertEqual(body, ["device_id": "dev", "name": "iPad"])
    }

    func testAskReportsRefusalAndFailure() {
        func ask(_ reply: FakeTransport.Reply) -> Asked {
            Pairing(transport: FakeTransport(["POST /pair/request": [reply]])).ask(host: "h", port: 1, deviceId: "d", deviceName: "n")
        }
        XCTAssertEqual(ask(.response(429, #"{"error":"cooldown"}"#)), .refused(reason: "cooldown"))
        XCTAssertEqual(ask(.response(500, "")), .failed(reason: "HTTP 500"))
        XCTAssertEqual(ask(.response(200, "nope")), .failed(reason: "unreadable answer"))
        XCTAssertEqual(ask(.failure("URLError: timed out")), .failed(reason: "URLError: timed out"))
    }

    func testPollPutsTheRequestIdInTheQueryAndTreatsALostPacketAsNotYet() {
        let transport = FakeTransport(["GET /pair/status": [.failure("lost"), .response(200, #"{"state":"approved","token":"x"}"#)]])
        let pairing = Pairing(transport: transport)
        XCTAssertEqual(pairing.poll(host: "h", port: 9671, requestId: "a b&c"), .pending)
        XCTAssertEqual(pairing.poll(host: "h", port: 9671, requestId: "r"), .allowed(token: "x"))
        XCTAssertEqual(transport.calls.first?.url, "http://h:9671/pair/status?request_id=a%20b%26c")
    }

    func testTrustedReadsTheStatusOfTheRootPage() {
        func trust(_ reply: FakeTransport.Reply) -> Trust {
            Pairing(transport: FakeTransport(["GET /": [reply]])).trusted(host: "h", port: 9671, token: "t")
        }
        XCTAssertEqual(trust(.response(200, "")), .yes)
        XCTAssertEqual(trust(.response(401, "")), .no)
        XCTAssertEqual(trust(.response(502, "")), .unreachable(reason: "HTTP 502"))
        XCTAssertEqual(trust(.failure("refused")), .unreachable(reason: "refused"))
    }

    func testTrustedSendsTheToken() {
        let transport = FakeTransport(["GET /": [.response(200, "")]])
        _ = Pairing(transport: transport).trusted(host: "h", port: 9671, token: "abc")
        XCTAssertEqual(transport.calls.first?.headers["Authorization"], "Basic a3Zpc3Q6YWJj")
    }

    func testABadAddressNeverReachesTheNetwork() {
        let transport = FakeTransport()
        XCTAssertEqual(Pairing(transport: transport).trusted(host: "not a host", port: 9671, token: "t"), .unreachable(reason: "invalid address"))
        XCTAssertTrue(transport.calls.isEmpty)
    }
}
