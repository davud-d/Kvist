import Foundation

/// What the connect screen (`connect.js`, shared with the Android app) asks of the app.
public enum BridgeRequest: Equatable, Sendable {
    case saved
    case scan
    case pair(PairRequest)
    case forget

    public static func parse(_ text: String) -> BridgeRequest? {
        guard let json = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] else { return nil }
        switch json["op"] as? String {
        case "saved": return .saved
        case "scan": return .scan
        case "forget": return .forget
        case "pair":
            let port = (json["port"] as? Int) ?? 9671
            return .pair(PairRequest(host: json["host"] as? String ?? "", port: port, id: json["id"] as? String ?? "",
                                     name: json["name"] as? String ?? "", auto: json["auto"] as? Bool ?? false))
        default: return nil
        }
    }
}

public enum BridgeReply: Equatable, Sendable {
    case saved(Saved?)
    case found([Found])
    case paired(Saved)
    case pairFailed(reason: String, detail: String?)

    /// The message the page reads. A `paired` reply carries nothing: the page is about to be replaced.
    public var json: String {
        let object: [String: Any]
        switch self {
        case .saved(let saved):
            object = ["type": "saved", "saved": saved.map { ["id": $0.id, "name": $0.name, "host": $0.host, "port": $0.port] as [String: Any] } ?? NSNull()]
        case .found(let list):
            object = ["type": "found", "computers": list.map { entry in
                ["id": entry.computer.id, "name": entry.computer.name, "host": entry.computer.host, "port": entry.computer.port,
                 "os": entry.computer.os, "os_name": entry.computer.osName, "user": entry.computer.user, "saved": entry.saved] as [String: Any]
            }]
        case .paired:
            object = ["type": "paired"]
        case .pairFailed(let reason, let detail):
            var failed: [String: Any] = ["type": "pair-failed", "reason": reason]
            if let detail { failed["detail"] = detail }
            object = failed
        }
        let data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return String(decoding: data, as: UTF8.self)
    }
}

/// Answers the connect screen. Blocking for `scan` and `pair`: call it off the main thread.
public final class PageBridge {
    private let connector: Connector
    private let store: SavedStore

    public init(connector: Connector, store: SavedStore) {
        self.connector = connector
        self.store = store
    }

    public func respond(to request: BridgeRequest) -> BridgeReply? {
        switch request {
        case .saved:
            return .saved(store.load())
        case .scan:
            return .found(connector.scan())
        case .forget:
            store.forget()
            return nil
        case .pair(let pair):
            switch connector.pair(pair) {
            case .opened(let saved): return .paired(saved)
            case .failed(let reason, let detail): return .pairFailed(reason: reason, detail: detail)
            }
        }
    }
}
