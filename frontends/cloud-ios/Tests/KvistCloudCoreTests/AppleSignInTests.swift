import XCTest
@testable import KvistCloudCore

final class AppleSignInTests: XCTestCase {
    func testTheTokenCodeAndNameGoToThePageAsText() {
        let reply = AppleSignInReply.signedIn(identityToken: Data("a.b.c".utf8), authorizationCode: Data("code".utf8),
                                              givenName: "Ada", familyName: " Lovelace ")
        XCTAssertEqual(reply["identityToken"] as? String, "a.b.c")
        XCTAssertEqual(reply["authorizationCode"] as? String, "code")
        XCTAssertEqual(reply["name"] as? String, "Ada Lovelace")
    }

    func testLaterSignInsHaveNoNameAndThatIsFine() {
        let reply = AppleSignInReply.signedIn(identityToken: Data("a.b.c".utf8), authorizationCode: nil,
                                              givenName: nil, familyName: nil)
        XCTAssertEqual(reply["name"] as? String, "")
        XCTAssertEqual(reply["authorizationCode"] as? String, "")
    }

    func testNoTokenIsAFailureNotAnEmptySignIn() {
        let reply = AppleSignInReply.signedIn(identityToken: nil, authorizationCode: Data("c".utf8), givenName: nil,
                                              familyName: nil)
        XCTAssertEqual(reply["error"] as? String, "apple_failed")
        XCTAssertEqual(AppleSignInReply.cancelled()["cancelled"] as? Bool, true)
    }
}
