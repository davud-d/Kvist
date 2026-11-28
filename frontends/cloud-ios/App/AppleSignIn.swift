import AuthenticationServices
import CryptoKit
import KvistCloudCore
import UIKit

/// Apple's own sign-in sheet (App Review Guideline 4.8: offered next to GitHub). The nonce is the server's; Apple gets
/// its SHA-256, and the server checks the token carries exactly that, so a token cannot be replayed. The reply goes to
/// the page, which sends it to the server (`/api/auth/apple`); nothing is kept here.
final class AppleSignIn: NSObject, ASAuthorizationControllerDelegate, ASAuthorizationControllerPresentationContextProviding {
    private weak var anchor: UIViewController?
    private var reply: (([String: Any]) -> Void)?

    init(anchor: UIViewController) {
        self.anchor = anchor
    }

    func start(nonce: String, reply: @escaping ([String: Any]) -> Void) {
        guard self.reply == nil, !nonce.isEmpty else {
            reply(AppleSignInReply.failed())
            return
        }
        self.reply = reply
        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = SHA256.hash(data: Data(nonce.utf8)).map { String(format: "%02x", $0) }.joined()
        let controller = ASAuthorizationController(authorizationRequests: [request])
        controller.delegate = self
        controller.presentationContextProvider = self
        controller.performRequests()
    }

    func authorizationController(controller: ASAuthorizationController,
                                 didCompleteWithAuthorization authorization: ASAuthorization) {
        guard let credential = authorization.credential as? ASAuthorizationAppleIDCredential else {
            finish(AppleSignInReply.failed())
            return
        }
        finish(AppleSignInReply.signedIn(identityToken: credential.identityToken,
                                         authorizationCode: credential.authorizationCode,
                                         givenName: credential.fullName?.givenName,
                                         familyName: credential.fullName?.familyName))
    }

    func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        let cancelled = (error as? ASAuthorizationError)?.code == .canceled
        finish(cancelled ? AppleSignInReply.cancelled() : AppleSignInReply.failed())
    }

    func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        anchor?.view.window ?? ASPresentationAnchor()
    }

    private func finish(_ answer: [String: Any]) {
        let send = reply
        reply = nil
        send?(answer)
    }
}
