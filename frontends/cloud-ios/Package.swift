// swift-tools-version:5.9
import PackageDescription

// Everything the app does that is not UIKit or PassKit: the words the page and the app say to each other, where the
// web view may go, and the shape of an Apple Pay request and of the token handed back. Foundation only, so
// `swift test` runs it on Linux and macOS alike; the app target (App/) adds the web view and the payment sheet.
let package = Package(
    name: "KvistCloudCore",
    platforms: [.iOS(.v15), .macOS(.v13)],
    products: [.library(name: "KvistCloudCore", targets: ["KvistCloudCore"])],
    targets: [
        .target(name: "KvistCloudCore"),
        .testTarget(name: "KvistCloudCoreTests", dependencies: ["KvistCloudCore"]),
    ]
)
