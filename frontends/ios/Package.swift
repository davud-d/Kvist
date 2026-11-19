// swift-tools-version:5.9
import PackageDescription

// Everything the app does that is not UIKit: finding computers, asking them to trust the phone, the trusted
// computer's storage contract and the connect screen's message channel. It uses only Foundation and BSD
// sockets, so `swift test` runs it on Linux and macOS alike; the app target (App/) adds the web view on top.
let package = Package(
    name: "KvistCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "KvistCore", targets: ["KvistCore"])],
    targets: [
        .target(name: "KvistCore"),
        .testTarget(name: "KvistCoreTests", dependencies: ["KvistCore"]),
    ]
)
