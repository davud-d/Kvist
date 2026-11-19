import UIKit
import WebKit
import os
import KvistCore

/// One web view and no agent. Two pages live in it: the app's own connect screen (find a computer, ask it to
/// trust this device) and, once trusted, the page that computer serves at /m/. The connect screen talks to this
/// class through a message channel that only the app's own pages may use, so a computer's page cannot reach it.
/// The same arrangement as the Android app (frontends/android); the connect screen is the same file.
final class WebViewController: UIViewController {
    private static let computerScheme = "http"
    private static let switchURL = (scheme: "kvist-app", host: "switch")
    private static let background = UIColor(red: 0x17 / 255, green: 0x14 / 255, blue: 0x2A / 255, alpha: 1)

    // The connect screen's `window.KvistNative`, as the Android app's WebView offers it. It exists only on the
    // app's own pages; the native side checks the sender's origin as well, which is the real boundary.
    private static let bridgeScript = """
    (function () {
      if (location.protocol !== '\(AssetSchemeHandler.scheme):') return;
      window.KvistNative = {
        onmessage: null,
        postMessage: function (text) { window.webkit.messageHandlers.kvist.postMessage(text); }
      };
    })();
    """

    private let store = KeychainStore()
    private let log = Logger(subsystem: "site.kvist.ios", category: "app")
    private let network = DispatchQueue(label: "site.kvist.network", qos: .userInitiated)
    private lazy var bridge: PageBridge = {
        let connector = Connector(store: store, deviceName: UIDevice.current.model, log: { [log] message in log.notice("\(message, privacy: .public)") })
        return PageBridge(connector: connector, store: store)
    }()

    private var webView: WKWebView!
    private var current: Saved?
    private var urlObservation: NSKeyValueObservation?

    override func viewDidLoad() {
        super.viewDidLoad()
        // The interface is dark: light status bar and a dark keyboard.
        overrideUserInterfaceStyle = .dark
        view.backgroundColor = Self.background

        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: Self.bridgeScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        content.add(WeakScriptHandler(self), name: "kvist")
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = content
        configuration.setURLSchemeHandler(AssetSchemeHandler(), forURLScheme: AssetSchemeHandler.scheme)
        // The computer's page shows a "change computer" link only inside this app.
        configuration.applicationNameForUserAgent = "KvistApp/1"

        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.scrollView.bounces = false
        #if DEBUG
        if #available(iOS 16.4, *) { webView.isInspectable = true }
        #endif
        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        // The bottom edge is the keyboard's top: the page shrinks above the keyboard (also a floating one on an
        // iPad) instead of being scrolled under it, so the composer stays in view.
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            webView.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor),
        ])
        urlObservation = webView.observe(\.url, options: [.new]) { [weak self] _, _ in self?.updateBackGesture() }

        if let saved = store.load() { openComputer(saved) } else { showConnect() }
    }

    // ---- pages -----------------------------------------------------------------------------------------

    private func showConnect(error: String? = nil, detail: String? = nil) {
        current = nil
        var page = URLComponents()
        page.scheme = AssetSchemeHandler.scheme
        page.host = AssetSchemeHandler.host
        page.path = "/assets/connect.html"
        var query: [URLQueryItem] = []
        if let error { query.append(URLQueryItem(name: "err", value: error)) }
        if let detail { query.append(URLQueryItem(name: "detail", value: detail)) }
        page.queryItems = query.isEmpty ? nil : query
        guard let url = page.url else { return log.error("the connect page address could not be made") }
        webView.load(URLRequest(url: url))
    }

    private func openComputer(_ saved: Saved) {
        current = saved
        store.save(saved)
        var page = URLComponents()
        page.scheme = Self.computerScheme
        page.host = saved.host
        page.port = saved.port
        page.path = "/m/"
        guard let url = page.url else {
            log.error("the computer's address is not a valid URL")
            return showConnect(error: "unreachable", detail: "\(saved.host):\(saved.port)")
        }
        webView.load(URLRequest(url: url))
    }

    /// Only the app's own pages and the paired computer: the device is not a browser.
    private func allowed(_ url: URL) -> Bool {
        if url.scheme == AssetSchemeHandler.scheme { return url.host == AssetSchemeHandler.host }
        guard url.scheme == Self.computerScheme, let saved = current else { return false }
        return url.host == saved.host && (url.port ?? 80) == saved.port
    }

    // The edge swipe goes from a conversation back to the list (the page keeps its screens in the address),
    // but never from the list back to the connect screen.
    private func updateBackGesture() {
        let here = webView.url?.scheme == Self.computerScheme
        let before = webView.backForwardList.backList.last?.url.scheme == Self.computerScheme
        webView.allowsBackForwardNavigationGestures = here && before
    }

    // ---- the connect screen's channel --------------------------------------------------------------------

    fileprivate func handle(_ message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == AssetSchemeHandler.scheme, origin.host == AssetSchemeHandler.host,
              let text = message.body as? String, let request = BridgeRequest.parse(text) else { return }
        network.async { [weak self] in
            guard let self, let reply = self.bridge.respond(to: request) else { return }
            DispatchQueue.main.async { self.deliver(reply) }
        }
    }

    private func deliver(_ reply: BridgeReply) {
        if case .paired(let saved) = reply { return openComputer(saved) }
        // The page may have been replaced while the network worked.
        guard webView.url?.scheme == AssetSchemeHandler.scheme, let literal = try? JSONEncoder().encode(reply.json) else { return }
        let script = "window.KvistNative && window.KvistNative.onmessage && window.KvistNative.onmessage({ data: \(String(decoding: literal, as: UTF8.self)) });"
        webView.evaluateJavaScript(script) { [log] _, error in
            if let error { log.error("could not hand the reply to the page: \(error.localizedDescription, privacy: .public)") }
        }
    }
}

// ---- navigation ------------------------------------------------------------------------------------------------

extension WebViewController: WKNavigationDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.cancel) }
        if url.scheme == Self.switchURL.scheme, url.host == Self.switchURL.host {
            store.forget()
            showConnect()
            return decisionHandler(.cancel)
        }
        decisionHandler(allowed(url) ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodHTTPBasic else { return completionHandler(.performDefaultHandling, nil) }
        // A second challenge after the token was sent means the computer no longer knows this device (it was
        // forgotten there): ask again from the list.
        guard let saved = current, space.host == saved.host, challenge.previousFailureCount == 0 else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            store.forget()
            showConnect(error: "forgotten")
            return
        }
        completionHandler(.useCredential, URLCredential(user: "kvist", password: saved.token, persistence: .forSession))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let failure = error as NSError
        // Our own refusals (a link out of the paired computer, the forgotten flow) end as "cancelled".
        if failure.domain == NSURLErrorDomain && failure.code == NSURLErrorCancelled { return }
        guard current != nil else { return }
        showConnect(error: "unreachable", detail: failure.localizedDescription)
    }

    // iOS ends a web view's process under memory pressure and leaves a blank page.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let saved = current { openComputer(saved) } else { showConnect() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("(document.querySelector('meta[name=theme-color]')||{}).content||''") { [weak self] value, _ in
            guard let color = (value as? String).flatMap(UIColor.init(hex:)) else { return }
            self?.view.backgroundColor = color
        }
    }
}

/// The user content controller keeps its handlers alive; holding the controller weakly keeps it from owning its own web view.
private final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    private weak var target: WebViewController?

    init(_ target: WebViewController) { self.target = target }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.handle(message)
    }
}

private extension UIColor {
    /// "#rrggbb"; nil for anything else.
    convenience init?(hex: String) {
        guard hex.hasPrefix("#"), hex.count == 7, let value = UInt32(hex.dropFirst(), radix: 16) else { return nil }
        self.init(red: CGFloat((value >> 16) & 0xFF) / 255, green: CGFloat((value >> 8) & 0xFF) / 255, blue: CGFloat(value & 0xFF) / 255, alpha: 1)
    }
}
