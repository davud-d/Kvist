import XCTest
@testable import KvistCore

final class NetworkTests: XCTestCase {
    func testIPv4ParsesAndPrints() {
        XCTAssertEqual(IPv4("192.168.1.20")?.description, "192.168.1.20")
        XCTAssertEqual(IPv4("255.255.255.255")?.value, 0xFFFF_FFFF)
        for bad in ["", "1.2.3", "1.2.3.4.5", "256.1.1.1", "a.b.c.d", "1.2.3.-4"] {
            XCTAssertNil(IPv4(bad), bad)
        }
    }

    func testBroadcastOfASubnet() {
        XCTAssertEqual(Subnet(address: IPv4("192.168.1.20")!, prefix: 24).broadcast.description, "192.168.1.255")
        XCTAssertEqual(Subnet(address: IPv4("10.0.5.9")!, prefix: 22).broadcast.description, "10.0.7.255")
        XCTAssertEqual(Subnet(address: IPv4("10.1.2.3")!, prefix: 16).broadcast.description, "10.1.255.255")
        XCTAssertEqual(Subnet(address: IPv4("10.1.2.3")!, prefix: 0).broadcast.description, "255.255.255.255")
        XCTAssertEqual(Subnet(address: IPv4("10.1.2.3")!, prefix: 32).broadcast.description, "10.1.2.3")
    }

    func testAHomeSubnetSweepsEveryOtherHost() {
        let hosts = Subnet(address: IPv4("192.168.1.20")!, prefix: 24).hosts()
        XCTAssertEqual(hosts.count, 253)
        XCTAssertEqual(hosts.first?.description, "192.168.1.1")
        XCTAssertEqual(hosts.last?.description, "192.168.1.254")
        XCTAssertFalse(hosts.contains(IPv4("192.168.1.20")!))
    }

    func testABigSubnetSweepsOnlyTheNeighbourhood() {
        let hosts = Subnet(address: IPv4("10.1.2.3")!, prefix: 16).hosts()
        XCTAssertEqual(hosts.count, 1021)
        XCTAssertEqual(hosts.first?.description, "10.1.0.1")
        XCTAssertEqual(hosts.last?.description, "10.1.3.254")
    }

    func testATinySubnetHasNothingToSweep() {
        XCTAssertTrue(Subnet(address: IPv4("10.0.0.1")!, prefix: 31).hosts().isEmpty)
        XCTAssertTrue(Subnet(address: IPv4("10.0.0.1")!, prefix: 32).hosts().isEmpty)
    }

    func testTargetsStartWithTheLimitedBroadcastAndHaveNoDuplicates() {
        let home = Subnet(address: IPv4("192.168.1.20")!, prefix: 24)
        let targets = Discovery.targets(for: [home, home])
        XCTAssertEqual(targets.first?.description, "255.255.255.255")
        XCTAssertEqual(targets[1].description, "192.168.1.255")
        XCTAssertEqual(targets.count, 1 + 1 + 253)
        XCTAssertEqual(Set(targets).count, targets.count)
    }

    func testLocalSubnetsAreWellFormed() {
        for subnet in Subnet.local() {
            XCTAssertTrue((0...32).contains(subnet.prefix))
            XCTAssertNotEqual(subnet.address.description, "127.0.0.1")
        }
    }
}
