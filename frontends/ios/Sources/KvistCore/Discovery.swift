import Foundation
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif

/// What a computer says about itself: enough for a person to pick the right one from a list.
public struct Computer: Equatable, Sendable {
    public let id: String
    public let name: String
    public let host: String
    public let port: Int
    public let os: String
    public let osName: String
    public let user: String

    public init(id: String, name: String, host: String, port: Int, os: String, osName: String, user: String) {
        self.id = id
        self.name = name
        self.host = host
        self.port = port
        self.os = os
        self.osName = osName
        self.user = user
    }
}

public enum DiscoveryError: Error, Equatable {
    case socket(errno: Int32)
}

public struct ScanResult: Equatable, Sendable {
    public var computers: [Computer]
    public var sent: Int
    public var failed: Int
}

/// Finds computers running `kvist-web --lan` on the same router. A probe goes to the subnet's broadcast
/// address and, one by one, to every address of the subnet: iOS does not let an app broadcast without an
/// entitlement Apple grants by request, but it lets any app send to a single address on the local network
/// (the person is asked once). Every Kvist answers with its name and port; a computer's address is the sender
/// of its answer, so one with several adapters is reached by the one the phone can see.
public enum Discovery {
    public static let beaconPort: UInt16 = 9672
    private static let probe = Array("KVIST-DISCOVER/1".utf8)
    private static let receiveSliceMs: Int32 = 150
    // A sweep is a burst of datagrams; the radio's queue overflows if they leave in one breath.
    private static let burst = 32
    private static let burstPauseUs: UInt32 = 2_000

    public static func parseReply(_ data: Data, sender: String) -> Computer? {
        guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              json["service"] as? String == "kvist-web",
              let port = json["port"] as? Int, (1...65535).contains(port) else { return nil }
        func text(_ key: String) -> String { json[key] as? String ?? "" }
        func named(_ key: String) -> String { text(key).isEmpty ? sender : text(key) }
        return Computer(id: named("id"), name: named("name"), host: sender, port: port,
                        os: text("os"), osName: text("os_name"), user: text("user"))
    }

    /// Where a scan sends its probe: the limited broadcast, each subnet's broadcast and every host of it.
    public static func targets(for subnets: [Subnet]) -> [IPv4] {
        var seen = Set<IPv4>()
        var list: [IPv4] = []
        for target in [IPv4(0xFFFF_FFFF)] + subnets.flatMap({ [$0.broadcast] + $0.hosts() }) where seen.insert(target).inserted {
            list.append(target)
        }
        return list
    }

    /// Blocking: sends the probe to every target, then listens for `window` seconds. Call it off the main thread.
    public static func scan(targets: [IPv4], port: UInt16 = beaconPort, window: TimeInterval, resendAfter: TimeInterval = 0.6) throws -> ScanResult {
        #if canImport(Glibc)
        let descriptor = socket(AF_INET, Int32(SOCK_DGRAM.rawValue), 0)
        #else
        let descriptor = socket(AF_INET, SOCK_DGRAM, 0)
        #endif
        guard descriptor >= 0 else { throw DiscoveryError.socket(errno: errno) }
        defer { close(descriptor) }
        var on: Int32 = 1
        setsockopt(descriptor, SOL_SOCKET, SO_BROADCAST, &on, socklen_t(MemoryLayout<Int32>.size))

        var result = ScanResult(computers: [], sent: 0, failed: 0)
        var byId: [String: Int] = [:]
        let started = Date()
        sendProbes(descriptor, to: targets, port: port, into: &result)
        var resent = false
        while Date().timeIntervalSince(started) < window {
            // UDP drops packets, and a phone's radio may be asleep for the first one.
            if !resent && Date().timeIntervalSince(started) > resendAfter {
                sendProbes(descriptor, to: targets, port: port, into: &result)
                resent = true
            }
            var watched = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            let ready = poll(&watched, 1, receiveSliceMs)
            if ready < 0 {
                if errno == EINTR { continue }
                throw DiscoveryError.socket(errno: errno)
            }
            guard ready > 0, let (data, sender) = receive(descriptor) else { continue }
            guard let computer = parseReply(data, sender: sender.description) else { continue }
            if let index = byId[computer.id] {
                result.computers[index] = computer
            } else {
                byId[computer.id] = result.computers.count
                result.computers.append(computer)
            }
        }
        return result
    }

    // A target the network refuses (a broadcast without the entitlement) is counted, and the rest still go out.
    private static func sendProbes(_ descriptor: Int32, to targets: [IPv4], port: UInt16, into result: inout ScanResult) {
        for (index, target) in targets.enumerated() {
            var address = sockaddr_in()
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = port.bigEndian
            address.sin_addr = in_addr(s_addr: target.value.bigEndian)
            #if canImport(Darwin)
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            #endif
            let count = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sendto(descriptor, probe, probe.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
            if count < 0 { result.failed += 1 } else { result.sent += 1 }
            if index % burst == burst - 1 { usleep(burstPauseUs) }
        }
    }

    private static func receive(_ descriptor: Int32) -> (Data, IPv4)? {
        var buffer = [UInt8](repeating: 0, count: 512)
        var from = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let count = withUnsafeMutablePointer(to: &from) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { recvfrom(descriptor, &buffer, buffer.count, 0, $0, &length) }
        }
        guard count > 0 else { return nil }
        return (Data(buffer[0..<count]), IPv4(UInt32(bigEndian: from.sin_addr.s_addr)))
    }
}
