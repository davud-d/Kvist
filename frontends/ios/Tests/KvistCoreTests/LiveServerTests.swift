import XCTest
@testable import KvistCore

/// The Swift side against the real Python side: `kvist_web.lan.Beacon` and the real HTTP pairing routes.
/// Skipped unless a computer is running: set KVIST_LIVE_BEACON_PORT (UDP) and KVIST_LIVE_HTTP_PORT (the same
/// port the beacon announces). scripts/live-check.sh starts one on loopback and runs this test.
final class LiveServerTests: XCTestCase {
    func testScanPairReopenAndForgottenAgainstTheRealComputer() throws {
        let env = ProcessInfo.processInfo.environment
        guard let beacon = env["KVIST_LIVE_BEACON_PORT"].flatMap(UInt16.init), let http = env["KVIST_LIVE_HTTP_PORT"].flatMap(Int.init) else {
            throw XCTSkip("no live computer: run scripts/live-check.sh")
        }
        let store = MemoryStore()
        // 127.0.0.1/32: the probe goes to loopback only, never to the network the test runs on.
        let connector = Connector(store: store, deviceName: "iPad", subnets: { [Subnet(address: IPv4("127.0.0.1")!, prefix: 32)] },
                                  log: { print("log:", $0) }, scanWindow: 1.5, beaconPort: beacon)
        let found = connector.scan()
        let computer = try XCTUnwrap(found.first, "the beacon did not answer")
        XCTAssertEqual(found.count, 1)
        XCTAssertEqual(computer.computer.port, http)
        XCTAssertEqual(computer.computer.host, "127.0.0.1")
        XCTAssertEqual(computer.computer.osName, "Test OS 1")
        XCTAssertFalse(computer.saved)
        // A phone the computer does not know: the (scripted) owner allows it.
        let request = PairRequest(host: computer.computer.host, port: http, id: computer.computer.id, name: computer.computer.name)
        guard case .opened(let saved) = connector.pair(request) else { return XCTFail("pairing did not open") }
        XCTAssertFalse(saved.token.isEmpty)
        store.save(saved)
        // The app starting up: the token it holds is enough, and nobody is asked.
        XCTAssertEqual(connector.pair(PairRequest(host: computer.computer.host, port: http, id: computer.computer.id, name: "desk", auto: true)),
                       .opened(Saved(id: computer.computer.id, name: "desk", host: "127.0.0.1", port: http, token: saved.token)))
        XCTAssertTrue(connector.scan().first?.saved == true, "the list marks the paired computer")
        // The computer forgot this phone (kvist-web --forget-phones).
        store.save(Saved(id: saved.id, name: saved.name, host: saved.host, port: saved.port, token: "revoked"))
        XCTAssertEqual(connector.pair(PairRequest(host: computer.computer.host, port: http, id: computer.computer.id, name: "desk", auto: true)),
                       .failed(reason: "forgotten"))
        // Nothing listens there.
        XCTAssertEqual({ () -> String? in
            if case .failed(let reason, _) = connector.pair(PairRequest(host: "127.0.0.1", port: 1, id: "other")) { return reason }
            return nil
        }(), "unreachable")
    }
}
