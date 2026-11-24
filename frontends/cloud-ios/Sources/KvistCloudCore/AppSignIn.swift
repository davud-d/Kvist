import Foundation

/// Signing in from the app (cloud/backend/kvist_cloud/appauth.py). GitHub's page, and Google's behind "sign in with
/// Google", will not run in a web view, so the system browser's own sign-in session does it and hands the app a short-lived
/// code; the web view trades the code (and the nonce only this app knows) for its session cookie.
public enum AppSignIn {
    public static let scheme = "kvistcloud"

    /// 32 random bytes as hex: made fresh for every attempt, never stored.
    public static func makeNonce() -> String {
        var generator = SystemRandomNumberGenerator()
        return (0..<32).map { _ in String(format: "%02x", UInt8.random(in: .min ... .max, using: &generator)) }.joined()
    }

    /// What the system browser opens.
    public static func startURL(cloudURL: String, nonce: String) -> URL? {
        var parts = URLComponents(string: cloudURL + "/auth/app/start")
        parts?.queryItems = [URLQueryItem(name: "nonce", value: nonce)]
        return parts?.url
    }

    /// The code in the address the system browser ends on (kvistcloud://signed-in?code=...), or nil for anything else.
    public static func code(from callback: URL) -> String? {
        guard callback.scheme?.lowercased() == scheme, callback.host?.lowercased() == "signed-in",
              let code = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems?
                .first(where: { $0.name == "code" })?.value, !code.isEmpty else { return nil }
        return code
    }

    /// What the web view loads to get its cookie, and then lands on the app's start page.
    public static func exchangeURL(cloudURL: String, code: String, nonce: String) -> URL? {
        var parts = URLComponents(string: cloudURL + "/auth/app/exchange")
        parts?.queryItems = [URLQueryItem(name: "code", value: code), URLQueryItem(name: "nonce", value: nonce),
                             URLQueryItem(name: "next", value: "/m/")]
        return parts?.url
    }
}
