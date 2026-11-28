import Foundation

/// What the page gets back from Apple's sign-in sheet (the app's `appleSignIn` call). Pure, so it is tested on Linux;
/// the sheet itself is in App/AppleSignIn.swift.
public enum AppleSignInReply {
    /// The identity token and authorization code as text, and the name Apple tells only on the first sign-in.
    public static func signedIn(identityToken: Data?, authorizationCode: Data?, givenName: String?,
                                familyName: String?) -> [String: Any] {
        guard let token = identityToken.flatMap({ String(data: $0, encoding: .utf8) }), !token.isEmpty else {
            return Bridge.failed("apple_failed")
        }
        let code = authorizationCode.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        let name = [givenName, familyName].compactMap { $0?.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }.joined(separator: " ")
        return ["identityToken": token, "authorizationCode": code, "name": name]
    }

    /// The person closed the sheet: not an error, the page just stays where it was.
    public static func cancelled() -> [String: Any] { Bridge.cancelled() }

    public static func failed() -> [String: Any] { Bridge.failed("apple_failed") }
}
