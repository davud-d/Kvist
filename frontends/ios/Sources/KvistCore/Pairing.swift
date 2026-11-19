import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// The trusted computer: who it is, where it was last seen and the token only this phone holds.
public struct Saved: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var host: String
    public var port: Int
    public var token: String

    public init(id: String, name: String, host: String, port: Int, token: String) {
        self.id = id
        self.name = name
        self.host = host
        self.port = port
        self.token = token
    }
}

public enum Answer: Equatable, Sendable {
    /// The owner allowed this phone; the token is shown once.
    case allowed(token: String)
    case pending
    case denied
    /// The computer has no way to ask its owner (no desktop session).
    case unavailable
    case expired
}

public enum Trust: Equatable, Sendable {
    case yes
    case no
    /// The computer did not answer as a Kvist does; `reason` is what the network said.
    case unreachable(reason: String)
}

public enum Asked: Equatable, Sendable {
    case id(String)
    /// "busy" (another phone is asking) or "cooldown" (the owner just said no).
    case refused(reason: String)
    case failed(reason: String)
}

public struct HTTPResponse: Equatable, Sendable {
    public var status: Int
    public var body: String

    public init(status: Int, body: String) {
        self.status = status
        self.body = body
    }
}

/// A request that never reached the computer, in the network's own words.
public struct TransportError: Error, Equatable, Sendable {
    public var reason: String

    public init(reason: String) { self.reason = reason }
}

public protocol HTTPTransport: Sendable {
    /// Blocking. Never follows a redirect: a Kvist computer does not redirect, so one means something else answered.
    func send(method: String, url: URL, headers: [String: String], body: Data?) throws -> HTTPResponse
}

public struct Pairing: Sendable {
    private let transport: HTTPTransport

    public init(transport: HTTPTransport = URLSessionTransport()) { self.transport = transport }

    public static func authHeader(token: String) -> String {
        "Basic " + Data("kvist:\(token)".utf8).base64EncodedString()
    }

    public static func parseAnswer(_ body: String) -> Answer {
        guard let json = (try? JSONSerialization.jsonObject(with: Data(body.utf8))) as? [String: Any] else { return .expired }
        switch json["state"] as? String {
        case "approved":
            guard let token = json["token"] as? String, !token.isEmpty else { return .expired }
            return .allowed(token: token)
        case "pending": return .pending
        case "denied": return .denied
        case "unavailable": return .unavailable
        default: return .expired
        }
    }

    /// Blocking: asks the computer to ask its owner. Call it off the main thread.
    public func ask(host: String, port: Int, deviceId: String, deviceName: String) -> Asked {
        let body = try? JSONSerialization.data(withJSONObject: ["device_id": deviceId, "name": deviceName])
        do {
            let response = try call(host: host, port: port, method: "POST", path: "/pair/request", body: body)
            let json = (try? JSONSerialization.jsonObject(with: Data(response.body.utf8))) as? [String: Any]
            if response.status == 200 {
                guard let id = json?["request_id"] as? String else { return .failed(reason: "unreadable answer") }
                return .id(id)
            }
            if response.status == 429 { return .refused(reason: json?["error"] as? String ?? "") }
            return .failed(reason: "HTTP \(response.status)")
        } catch let error as TransportError {
            return .failed(reason: error.reason)
        } catch {
            return .failed(reason: "\(error)")
        }
    }

    /// Blocking: has the owner answered yet? A lost packet is "not yet", the next poll asks again.
    public func poll(host: String, port: Int, requestId: String) -> Answer {
        guard let response = try? call(host: host, port: port, method: "GET", path: "/pair/status", query: [URLQueryItem(name: "request_id", value: requestId)]) else {
            return .pending
        }
        return Pairing.parseAnswer(response.body)
    }

    /// Blocking: does the computer still trust this phone?
    public func trusted(host: String, port: Int, token: String) -> Trust {
        do {
            let response = try call(host: host, port: port, method: "GET", path: "/", token: token)
            switch response.status {
            case 200: return .yes
            case 401: return .no
            default: return .unreachable(reason: "HTTP \(response.status)")
            }
        } catch let error as TransportError {
            return .unreachable(reason: error.reason)
        } catch {
            return .unreachable(reason: "\(error)")
        }
    }

    private func call(host: String, port: Int, method: String, path: String, query: [URLQueryItem] = [], body: Data? = nil, token: String? = nil) throws -> HTTPResponse {
        var parts = URLComponents()
        parts.scheme = "http"
        parts.host = host
        parts.port = port
        parts.path = path
        if !query.isEmpty { parts.queryItems = query }
        guard let url = parts.url else { throw TransportError(reason: "invalid address") }
        var headers: [String: String] = [:]
        if let token { headers["Authorization"] = Pairing.authHeader(token: token) }
        if body != nil { headers["Content-Type"] = "application/json" }
        return try transport.send(method: method, url: url, headers: headers, body: body)
    }
}

/// The real network. One short-lived session per call keeps a stalled computer from holding the next one up.
public struct URLSessionTransport: HTTPTransport {
    private let timeout: TimeInterval

    public init(timeout: TimeInterval = 4) {
        self.timeout = timeout
    }

    /// A computer that no longer knows this phone answers 401 with a Basic challenge. The token goes in the header
    /// by hand, so there is nothing to offer: the challenge is the answer. What a refused challenge looks like
    /// differs between URLSession implementations (a 401 response, a cancelled error, a stall), so it is noted
    /// here and turned into the 401 it stands for, the same on every platform.
    private final class Session: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
        var value: Result<HTTPResponse, TransportError>?
        var challenged = false

        func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
            challenged = true
            completionHandler(.cancelAuthenticationChallenge, nil)
        }

        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)
        }
    }

    public func send(method: String, url: URL, headers: [String: String], body: Data?) throws -> HTTPResponse {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.httpMethod = method
        request.httpBody = body
        for (name, value) in headers { request.setValue(value, forHTTPHeaderField: name) }
        let state = Session()
        let session = URLSession(configuration: .ephemeral, delegate: state, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let done = DispatchSemaphore(value: 0)
        session.dataTask(with: request) { data, response, error in
            if state.challenged {
                state.value = .success(HTTPResponse(status: 401, body: ""))
            } else if let error {
                state.value = .failure(TransportError(reason: "\(type(of: error)): \(error.localizedDescription)"))
            } else if let http = response as? HTTPURLResponse {
                state.value = .success(HTTPResponse(status: http.statusCode, body: String(decoding: data ?? Data(), as: UTF8.self)))
            } else {
                state.value = .failure(TransportError(reason: "not an HTTP answer"))
            }
            done.signal()
        }.resume()
        done.wait()
        return try state.value!.get()
    }
}
