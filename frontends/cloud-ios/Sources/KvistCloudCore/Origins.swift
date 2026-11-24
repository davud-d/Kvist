import Foundation

/// Where the web view may go. The cloud itself, and GitHub while the person signs in (the cloud's own sign-in is
/// GitHub's page, which must load in this window for the session cookie to come back here). Everything else (a
/// repository, a payment page, the terms) opens in the system browser: the app is not a browser, and the payment
/// bridge must never be reachable from a page that is not ours.
public enum Origins {
    private static let signInHosts: Set<String> = ["github.com", "www.github.com"]

    /// "https://cloud.example:8443" for a URL, or nil when it is not an http(s) address.
    public static func originOf(_ url: String) -> String? {
        guard let parts = URLComponents(string: url),
              let scheme = parts.scheme?.lowercased(), scheme == "https" || scheme == "http",
              let host = parts.host?.lowercased(), !host.isEmpty else { return nil }
        guard let port = parts.port, !(scheme == "https" && port == 443), !(scheme == "http" && port == 80) else {
            return "\(scheme)://\(host)"
        }
        return "\(scheme)://\(host):\(port)"
    }

    public static func isCloud(_ url: String, cloudURL: String) -> Bool {
        guard let origin = originOf(url) else { return false }
        return origin == originOf(cloudURL)
    }

    /// True when the web view may load the URL itself.
    public static func staysInApp(_ url: String, cloudURL: String) -> Bool {
        if isCloud(url, cloudURL: cloudURL) { return true }
        guard let parts = URLComponents(string: url), parts.scheme?.lowercased() == "https",
              let host = parts.host?.lowercased() else { return false }
        return signInHosts.contains(host)
    }

    /// Only web links and mail go out to other apps: never a custom scheme or a file path a page makes up.
    public static func mayOpenOutside(_ url: String) -> Bool {
        guard let scheme = URLComponents(string: url)?.scheme?.lowercased() else { return false }
        return scheme == "https" || scheme == "http" || scheme == "mailto"
    }

    /// The origin of a message's frame (WKSecurityOrigin's protocol, host and port, where 0 is the default port).
    public static func origin(scheme: String, host: String, port: Int) -> String? {
        originOf(port > 0 ? "\(scheme)://\(host):\(port)" : "\(scheme)://\(host)")
    }
}
