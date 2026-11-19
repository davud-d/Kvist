import Foundation

/// Where the trusted computer and this phone's own identity are kept. The app keeps them in the Keychain.
public protocol SavedStore: AnyObject {
    /// Made once and never changed: it is how a computer tells this phone from another.
    func deviceId() -> String
    func load() -> Saved?
    func save(_ saved: Saved)
    /// Forgets the computer, not the phone's own identity.
    func forget()
}

public struct Found: Equatable, Sendable {
    public var computer: Computer
    public var saved: Bool
}

public struct PairRequest: Equatable, Sendable {
    public var host: String
    public var port: Int
    public var id: String
    public var name: String
    /// The app starting up by itself: it must not make a window appear on the computer, so it never asks.
    public var auto: Bool

    public init(host: String, port: Int = 9671, id: String = "", name: String = "", auto: Bool = false) {
        let cleaned = host.trimmingCharacters(in: .whitespaces)
        self.host = cleaned
        self.port = port
        self.id = id.isEmpty ? cleaned : id
        self.name = name.isEmpty ? cleaned : name
        self.auto = auto
    }
}

public enum PairOutcome: Equatable, Sendable {
    case opened(Saved)
    case failed(reason: String, detail: String? = nil)
}

/// The phone's side of getting trusted by a computer. The same rules as the Android app: a computer that
/// already trusts this phone opens at once, wherever the router put it now; otherwise the computer asks its
/// owner and this waits for the answer.
public final class Connector {
    private let store: SavedStore
    private let pairing: Pairing
    private let deviceName: String
    private let subnets: () -> [Subnet]
    private let log: (String) -> Void
    private let scanWindow: TimeInterval
    private let beaconPort: UInt16
    private let pollInterval: TimeInterval
    private let answerPolls: Int
    private let sleep: (TimeInterval) -> Void

    // The owner has a minute to answer; the computer's window gives up after the same time.
    public init(store: SavedStore, pairing: Pairing = Pairing(), deviceName: String,
                subnets: @escaping () -> [Subnet] = Subnet.local, log: @escaping (String) -> Void,
                scanWindow: TimeInterval = 1.6, beaconPort: UInt16 = Discovery.beaconPort,
                pollInterval: TimeInterval = 1, answerPolls: Int = 75, sleep: @escaping (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) }) {
        self.store = store
        self.pairing = pairing
        self.deviceName = deviceName
        self.subnets = subnets
        self.log = log
        self.scanWindow = scanWindow
        self.beaconPort = beaconPort
        self.pollInterval = pollInterval
        self.answerPolls = answerPolls
        self.sleep = sleep
    }

    /// Blocking. A scan that could not run is logged and shows as an empty list: the page offers the address by hand.
    public func scan() -> [Found] {
        let saved = store.load()
        let targets = Discovery.targets(for: subnets())
        do {
            let result = try Discovery.scan(targets: targets, port: beaconPort, window: scanWindow)
            if result.failed > 0 { log("discovery: \(result.failed) of \(result.sent + result.failed) probes were refused") }
            return result.computers.map { Found(computer: $0, saved: $0.id == saved?.id) }
        } catch {
            log("discovery failed: \(error)")
            return []
        }
    }

    /// Blocking.
    public func pair(_ request: PairRequest) -> PairOutcome {
        guard !request.host.isEmpty, (1...65535).contains(request.port) else { return .failed(reason: "unreachable") }
        let saved = store.load()
        if let saved, saved.id == request.id {
            switch pairing.trusted(host: request.host, port: request.port, token: saved.token) {
            case .yes:
                return .opened(Saved(id: request.id, name: request.name, host: request.host, port: request.port, token: saved.token))
            case .unreachable(let reason):
                return .failed(reason: "unreachable", detail: "\(request.host):\(request.port) \(reason)")
            case .no:
                if request.auto { return .failed(reason: "forgotten") }
            }
        } else if request.auto {
            return .failed(reason: "forgotten")
        }
        switch pairing.ask(host: request.host, port: request.port, deviceId: store.deviceId(), deviceName: deviceName) {
        case .refused(let reason): return .failed(reason: reason)
        case .failed(let reason): return .failed(reason: "unreachable", detail: "\(request.host):\(request.port) \(reason)")
        case .id(let requestId): return waitForOwner(request, requestId: requestId)
        }
    }

    private func waitForOwner(_ request: PairRequest, requestId: String) -> PairOutcome {
        for _ in 0..<answerPolls {
            switch pairing.poll(host: request.host, port: request.port, requestId: requestId) {
            case .allowed(let token):
                return .opened(Saved(id: request.id, name: request.name, host: request.host, port: request.port, token: token))
            case .denied: return .failed(reason: "denied")
            case .unavailable: return .failed(reason: "unavailable")
            case .expired: return .failed(reason: "expired")
            case .pending: sleep(pollInterval)
            }
        }
        return .failed(reason: "expired")
    }
}
