import XCTest
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif
@testable import KvistCore

final class DiscoveryTests: XCTestCase {
    private func reply(_ fields: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: fields)
    }

    func testAReplyBecomesAComputerAtTheSender() {
        let data = reply(["service": "kvist-web", "id": "abc", "name": "desk", "port": 9671, "os": "linux", "os_name": "Arch Linux", "user": "ann"])
        XCTAssertEqual(Discovery.parseReply(data, sender: "192.168.1.5"),
                       Computer(id: "abc", name: "desk", host: "192.168.1.5", port: 9671, os: "linux", osName: "Arch Linux", user: "ann"))
    }

    func testAMissingNameAndIdFallBackToTheSender() {
        let computer = Discovery.parseReply(reply(["service": "kvist-web", "port": 9671]), sender: "10.0.0.2")
        XCTAssertEqual(computer?.id, "10.0.0.2")
        XCTAssertEqual(computer?.name, "10.0.0.2")
        XCTAssertEqual(computer?.user, "")
    }

    func testAnythingThatIsNotAKvistIsIgnored() {
        XCTAssertNil(Discovery.parseReply(reply(["service": "printer", "port": 9671]), sender: "10.0.0.2"))
        XCTAssertNil(Discovery.parseReply(reply(["service": "kvist-web", "port": 0]), sender: "10.0.0.2"))
        XCTAssertNil(Discovery.parseReply(reply(["service": "kvist-web", "port": 70000]), sender: "10.0.0.2"))
        XCTAssertNil(Discovery.parseReply(reply(["service": "kvist-web"]), sender: "10.0.0.2"))
        XCTAssertNil(Discovery.parseReply(Data("not json".utf8), sender: "10.0.0.2"))
    }

    // A real UDP round trip over loopback: a beacon thread answers the probe the way kvist_web/lan.py does.
    func testAScanFindsABeaconOverRealSockets() throws {
        let beacon = try LoopbackBeacon(answer: reply(["service": "kvist-web", "id": "abc", "name": "desk", "port": 9671, "os": "macos", "os_name": "macOS 15", "user": "ann"]))
        defer { beacon.stop() }
        let result = try Discovery.scan(targets: [IPv4("127.0.0.1")!], port: beacon.port, window: 1.2, resendAfter: 0.3)
        XCTAssertEqual(result.computers.count, 1, "the resent probe is answered twice, the list holds one")
        XCTAssertEqual(result.computers.first?.host, "127.0.0.1")
        XCTAssertEqual(result.computers.first?.osName, "macOS 15")
        XCTAssertEqual(result.failed, 0)
        XCTAssertGreaterThanOrEqual(beacon.probes, 1)
        XCTAssertEqual(beacon.lastProbe, "KVIST-DISCOVER/1")
    }

    func testAScanOfASilentNetworkIsEmpty() throws {
        let result = try Discovery.scan(targets: [IPv4("127.0.0.1")!], port: 1, window: 0.4, resendAfter: 0.2)
        XCTAssertTrue(result.computers.isEmpty)
    }
}

/// A UDP socket on 127.0.0.1 that answers the discovery probe, standing in for `kvist-web --lan`.
final class LoopbackBeacon: @unchecked Sendable {
    let port: UInt16
    private let descriptor: Int32
    private let answer: Data
    private let lock = NSLock()
    private var running = true
    private var count = 0
    private var last = ""
    private var thread: Thread?

    var probes: Int { lock.lock(); defer { lock.unlock() }; return count }
    var lastProbe: String { lock.lock(); defer { lock.unlock() }; return last }

    init(answer: Data) throws {
        self.answer = answer
        #if canImport(Glibc)
        let fd = socket(AF_INET, Int32(SOCK_DGRAM.rawValue), 0)
        #else
        let fd = socket(AF_INET, SOCK_DGRAM, 0)
        #endif
        guard fd >= 0 else { throw DiscoveryError.socket(errno: errno) }
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr = in_addr(s_addr: UInt32(0x7F00_0001).bigEndian)
        let bound = withUnsafePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) } }
        guard bound == 0 else { throw DiscoveryError.socket(errno: errno) }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) } }
        var timeout = timeval(tv_sec: 0, tv_usec: 100_000)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        descriptor = fd
        port = UInt16(bigEndian: address.sin_port)
        let thread = Thread { [unowned self] in self.serve() }
        self.thread = thread
        thread.start()
    }

    private func serve() {
        while true {
            lock.lock(); let alive = running; lock.unlock()
            if !alive { return }
            var buffer = [UInt8](repeating: 0, count: 256)
            var from = sockaddr_in()
            var length = socklen_t(MemoryLayout<sockaddr_in>.size)
            let got = withUnsafeMutablePointer(to: &from) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { recvfrom(descriptor, &buffer, buffer.count, 0, $0, &length) } }
            guard got > 0 else { continue }
            lock.lock(); count += 1; last = String(decoding: buffer[0..<got], as: UTF8.self); lock.unlock()
            let bytes = [UInt8](answer)
            _ = withUnsafePointer(to: &from) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { sendto(descriptor, bytes, bytes.count, 0, $0, length) } }
        }
    }

    func stop() {
        lock.lock(); running = false; lock.unlock()
        // The thread leaves within one receive timeout; closing earlier would race its recvfrom.
        Thread.sleep(forTimeInterval: 0.25)
        close(descriptor)
    }
}
