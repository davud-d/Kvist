import Foundation
import KvistCore

final class MemoryStore: SavedStore {
    var saved: Saved?
    var forgotten = 0
    private let device = "device-1"

    init(saved: Saved? = nil) { self.saved = saved }

    func deviceId() -> String { device }
    func load() -> Saved? { saved }
    func save(_ saved: Saved) { self.saved = saved }
    func forget() {
        saved = nil
        forgotten += 1
    }
}

/// Answers from a script and remembers what it was asked. `answers` maps "METHOD /path" to a queue of replies;
/// the last reply repeats.
final class FakeTransport: HTTPTransport, @unchecked Sendable {
    enum Reply {
        case response(Int, String)
        case failure(String)
    }

    struct Call: Equatable {
        var method: String
        var url: String
        var headers: [String: String]
        var body: String
    }

    private let lock = NSLock()
    private var answers: [String: [Reply]]
    private(set) var calls: [Call] = []

    init(_ answers: [String: [Reply]] = [:]) { self.answers = answers }

    func send(method: String, url: URL, headers: [String: String], body: Data?) throws -> HTTPResponse {
        lock.lock()
        defer { lock.unlock() }
        calls.append(Call(method: method, url: url.absoluteString, headers: headers, body: body.map { String(decoding: $0, as: UTF8.self) } ?? ""))
        let key = "\(method) \(url.path)"
        guard var queue = answers[key], !queue.isEmpty else { throw TransportError(reason: "no script for \(key)") }
        let reply = queue.count > 1 ? queue.removeFirst() : queue[0]
        answers[key] = queue
        switch reply {
        case .response(let status, let text): return HTTPResponse(status: status, body: text)
        case .failure(let reason): throw TransportError(reason: reason)
        }
    }
}
