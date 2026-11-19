import Foundation
import WebKit

/// Serves the app's own pages (the connect screen and the pixel assets it shares with the computer's page) from
/// the bundle, at `kvist-assets://app/assets/...`: the same paths the computer's page uses, so one stylesheet
/// and one set of sprites serve both. Nothing outside the bundle's `web` folder can be named.
final class AssetSchemeHandler: NSObject, WKURLSchemeHandler {
    static let scheme = "kvist-assets"
    static let host = "app"
    private static let prefix = "/assets/"
    private static let types = [
        "html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
        "png": "image/png", "woff2": "font/woff2", "svg": "image/svg+xml", "json": "application/json",
    ]

    private let root: URL

    init(bundle: Bundle = .main) {
        root = (bundle.resourceURL ?? bundle.bundleURL).appendingPathComponent("web", isDirectory: true).standardizedFileURL
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return task.didFailWithError(URLError(.badURL)) }
        let file = resolve(url)
        let data = file.flatMap { try? Data(contentsOf: $0) }
        let type = file.flatMap { Self.types[$0.pathExtension.lowercased()] } ?? "text/plain"
        let headers = ["Content-Type": type, "Content-Length": String(data?.count ?? 0), "Cache-Control": "no-store"]
        guard let response = HTTPURLResponse(url: url, statusCode: data == nil ? 404 : 200, httpVersion: "HTTP/1.1", headerFields: headers) else {
            return task.didFailWithError(URLError(.badServerResponse))
        }
        task.didReceive(response)
        if let data { task.didReceive(data) }
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}

    private func resolve(_ url: URL) -> URL? {
        guard url.host == Self.host, url.path.hasPrefix(Self.prefix) else { return nil }
        let file = root.appendingPathComponent(String(url.path.dropFirst(Self.prefix.count))).standardizedFileURL
        return file.path.hasPrefix(root.path + "/") ? file : nil
    }
}
