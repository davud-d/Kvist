import Foundation
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif

public struct IPv4: Hashable, Sendable, CustomStringConvertible {
    public let value: UInt32

    public init(_ value: UInt32) { self.value = value }

    public init?(_ text: String) {
        let parts = text.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var value: UInt32 = 0
        for part in parts {
            guard let byte = UInt8(part) else { return nil }
            value = value << 8 | UInt32(byte)
        }
        self.value = value
    }

    public var description: String {
        "\(value >> 24).\((value >> 16) & 255).\((value >> 8) & 255).\(value & 255)"
    }
}

/// An IPv4 address with the length of its subnet prefix: where this device sits on the router's network.
public struct Subnet: Equatable, Sendable {
    public let address: IPv4
    public let prefix: Int

    /// Wi-Fi subnets are /24 at home and /16 in offices; a sweep of 65 000 addresses is not a polite thing to do.
    public static let widestSweepPrefix = 22

    public init(address: IPv4, prefix: Int) {
        self.address = address
        self.prefix = min(max(prefix, 0), 32)
    }

    private func mask(_ prefix: Int) -> UInt32 { prefix == 0 ? 0 : ~UInt32(0) << UInt32(32 - prefix) }

    public var broadcast: IPv4 { IPv4(address.value | ~mask(prefix)) }

    /// Every other address of the subnet (at most 1022), so a probe can be sent to each one by itself.
    public func hosts() -> [IPv4] {
        let effective = max(prefix, Subnet.widestSweepPrefix)
        guard effective < 31 else { return [] }
        let network = address.value & mask(effective)
        let last = network | ~mask(effective)
        return ((network + 1)..<last).filter { $0 != address.value }.map(IPv4.init)
    }

    /// The subnets this device is on through Wi-Fi or Ethernet. Cellular, VPN and peer-to-peer links are not
    /// the router's network and are left out.
    public static func local() -> [Subnet] {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return [] }
        defer { freeifaddrs(head) }
        // The same on Linux and Darwin: IFF_UP and IFF_LOOPBACK.
        let up: UInt32 = 0x1, loopback: UInt32 = 0x8
        let foreign = ["pdp_ip", "utun", "ipsec", "awdl", "llw", "ppp", "tun"]
        var found: [Subnet] = []
        var cursor: UnsafeMutablePointer<ifaddrs>? = first
        while let entry = cursor {
            cursor = entry.pointee.ifa_next
            let flags = UInt32(entry.pointee.ifa_flags)
            guard flags & up != 0, flags & loopback == 0 else { continue }
            guard let address = entry.pointee.ifa_addr, let netmask = entry.pointee.ifa_netmask,
                  address.pointee.sa_family == sa_family_t(AF_INET) else { continue }
            let name = String(cString: entry.pointee.ifa_name)
            if foreign.contains(where: { name.hasPrefix($0) }) { continue }
            let own = address.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { UInt32(bigEndian: $0.pointee.sin_addr.s_addr) }
            let bits = netmask.withMemoryRebound(to: sockaddr_in.self, capacity: 1) { UInt32(bigEndian: $0.pointee.sin_addr.s_addr) }
            found.append(Subnet(address: IPv4(own), prefix: bits.nonzeroBitCount))
        }
        return found
    }
}
