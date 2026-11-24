import XCTest
@testable import KvistCloudCore

final class OriginsTests: XCTestCase {
    private let cloud = "https://cloud.kvist.site"

    func testAnOriginIsSchemeHostAndPortWithoutTheStandardOnes() {
        XCTAssertEqual(Origins.originOf("https://cloud.kvist.site/m/s/abc?x=1"), "https://cloud.kvist.site")
        XCTAssertEqual(Origins.originOf("https://CLOUD.kvist.site:443/"), "https://cloud.kvist.site")
        XCTAssertEqual(Origins.originOf("http://127.0.0.1:8742/m/"), "http://127.0.0.1:8742")
        XCTAssertNil(Origins.originOf("kvist://x"))
        XCTAssertNil(Origins.originOf("not a url"))
        XCTAssertNil(Origins.originOf("file:///var/x.html"))
    }

    func testTheCloudAndOnlyTheCloudIsTheCloud() {
        XCTAssertTrue(Origins.isCloud("https://cloud.kvist.site/m/", cloudURL: cloud))
        XCTAssertFalse(Origins.isCloud("https://cloud.kvist.site.evil.example/m/", cloudURL: cloud))
        XCTAssertFalse(Origins.isCloud("http://cloud.kvist.site/m/", cloudURL: cloud))
        XCTAssertFalse(Origins.isCloud("https://cloud.kvist.site:8443/m/", cloudURL: cloud))
        XCTAssertFalse(Origins.isCloud("https://evil.example/https://cloud.kvist.site/", cloudURL: cloud))
        XCTAssertFalse(Origins.isCloud("https://cloud.kvist.site@evil.example/", cloudURL: cloud))
    }

    func testGithubStaysInTheAppOnlyForSigningIn() {
        XCTAssertTrue(Origins.staysInApp("https://github.com/login/oauth/authorize?client_id=x", cloudURL: cloud))
        XCTAssertTrue(Origins.staysInApp("https://cloud.kvist.site/auth/github/callback?code=1", cloudURL: cloud))
        XCTAssertFalse(Origins.staysInApp("http://github.com/login", cloudURL: cloud))
        XCTAssertFalse(Origins.staysInApp("https://github.com.evil.example/login", cloudURL: cloud))
        XCTAssertFalse(Origins.staysInApp("https://gist.github.com/x", cloudURL: cloud))
        XCTAssertFalse(Origins.staysInApp("https://pay.example/checkout", cloudURL: cloud))
    }

    func testOnlyWebLinksLeaveForOtherApps() {
        XCTAssertTrue(Origins.mayOpenOutside("https://platega.example/pay?id=1"))
        XCTAssertTrue(Origins.mayOpenOutside("mailto:support@cloud.example"))
        XCTAssertFalse(Origins.mayOpenOutside("tel:+10000000"))
        XCTAssertFalse(Origins.mayOpenOutside("file:///var/mobile/x"))
        XCTAssertFalse(Origins.mayOpenOutside("javascript:alert(1)"))
        XCTAssertFalse(Origins.mayOpenOutside(""))
    }

    func testAMessagesFrameOriginIsBuiltTheSameWay() {
        XCTAssertEqual(Origins.origin(scheme: "https", host: "cloud.kvist.site", port: 0), "https://cloud.kvist.site")
        XCTAssertEqual(Origins.origin(scheme: "http", host: "127.0.0.1", port: 8742), "http://127.0.0.1:8742")
        XCTAssertEqual(Origins.origin(scheme: "about", host: "", port: 0), nil)
    }
}
