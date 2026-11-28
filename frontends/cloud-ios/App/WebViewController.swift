import AuthenticationServices
import KvistCloudCore
import UIKit
import WebKit

/// One web view with Kvist Cloud's phone page in it, and the operating system's own things the page cannot do:
/// the Apple Pay sheet, links in the system browser, the clipboard, a tap's vibration.
///
/// The page reaches this class only through a message handler, and only a message from the cloud's own origin in the
/// main frame is answered (Bridge.startScript makes the handler look like `window.KvistHost`); GitHub's sign-in page and
/// anything else loaded here is refused.
final class WebViewController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply,
    ASWebAuthenticationPresentationContextProviding {
    private let background = UIColor(red: 10 / 255, green: 10 / 255, blue: 11 / 255, alpha: 1)
    private let cloudURL: String = (Bundle.main.object(forInfoDictionaryKey: "KvistCloudURL") as? String) ?? "https://cloud.kvist.site"
    // The App Store build (KVIST_STORE=YES): the page sells nothing in it (App Review Guideline 3.1.3(f)).
    private let storeBuild: Bool = (Bundle.main.object(forInfoDictionaryKey: "KvistStore") as? String) == "YES"
    private let applePay = ApplePay()
    private lazy var appleSignIn = AppleSignIn(anchor: self)
    private var webView: WKWebView!
    private var offline: UIView!
    private var signInSession: ASWebAuthenticationSession?

    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }

    override func viewDidLoad() {
        super.viewDidLoad()
        overrideUserInterfaceStyle = .dark
        view.backgroundColor = background

        let content = WKUserContentController()
        content.addUserScript(WKUserScript(source: Bridge.startScript(store: storeBuild), injectionTime: .atDocumentStart, forMainFrameOnly: true))
        content.addScriptMessageHandler(self, contentWorld: .page, name: Bridge.handlerName)
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = content
        configuration.applicationNameForUserAgent = "KvistCloudApp/1"
        configuration.websiteDataStore = .default()

        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = background
        webView.scrollView.backgroundColor = background
        // The page lays itself out under the bars and keeps clear of them with env(safe-area-inset-*) (viewport-fit=cover).
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.scrollView.bounces = false
        webView.allowsBackForwardNavigationGestures = false
        if #available(iOS 16.4, *) { webView.isInspectable = isDebugBuild }
        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        webView.hideKeyboardAccessoryBar()
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: view.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])

        offline = makeOfflineView()
        offline.isHidden = true
        view.addSubview(offline)
        offline.frame = view.bounds
        offline.autoresizingMask = [.flexibleWidth, .flexibleHeight]

        load()
    }

    private var isDebugBuild: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    private func load() {
        offline.isHidden = true
        guard let url = URL(string: cloudURL + "/m/") else { return }
        webView.load(URLRequest(url: url))
    }

    // MARK: the bridge

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage,
        replyHandler: @escaping (Any?, String?) -> Void
    ) {
        let frame = message.frameInfo
        let origin = frame.securityOrigin
        guard frame.isMainFrame,
              let sender = Origins.origin(scheme: origin.protocol, host: origin.host, port: origin.port),
              sender == Origins.originOf(cloudURL),
              let call = Bridge.parse(message.body) else {
            replyHandler(nil, "forbidden")
            return
        }
        switch call.name {
        case "wallets":
            replyHandler(ApplePay.isReady() ? ["apple_pay"] : [String](), nil)
        case "pay":
            guard let request = ApplePayRequest.parse(call.args["request"] as? [String: Any]) else {
                replyHandler(Bridge.failed("wallet_unavailable"), nil)
                return
            }
            applePay.pay(request) { replyHandler($0, nil) }
        case "signIn":
            signIn()
            replyHandler(["started": true], nil)
        case "appleSignIn":
            appleSignIn.start(nonce: call.args["nonce"] as? String ?? "") { replyHandler($0, nil) }
        case "payDone":
            applePay.finish(ok: call.args["ok"] as? Bool ?? false)
            replyHandler(nil, nil)
        case "open":
            open(call.args["url"] as? String ?? "")
            replyHandler(nil, nil)
        case "haptic":
            haptic(call.args["kind"] as? String ?? "")
            replyHandler(nil, nil)
        case "copy":
            UIPasteboard.general.string = call.args["text"] as? String ?? ""
            replyHandler(nil, nil)
        case "share":
            share(call.args["text"] as? String ?? "")
            replyHandler(nil, nil)
        default:
            replyHandler(nil, nil)
        }
    }

    // MARK: signing in

    /// GitHub's page, and Google's behind "sign in with Google", will not run in this web view: the system browser's own
    /// sign-in session does it (it shares Safari's cookies, so a person already signed in to GitHub there is not asked
    /// again), and hands back a code that this web view trades for its session (appauth.py).
    private func signIn() {
        let nonce = AppSignIn.makeNonce()
        guard signInSession == nil, let url = AppSignIn.startURL(cloudURL: cloudURL, nonce: nonce) else { return }
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: AppSignIn.scheme) { [weak self] callback, _ in
            guard let self else { return }
            self.signInSession = nil
            // Cancelled, or the system browser ended somewhere else: the page stays as it was.
            guard let callback, let code = AppSignIn.code(from: callback),
                  let exchange = AppSignIn.exchangeURL(cloudURL: self.cloudURL, code: code, nonce: nonce) else { return }
            self.webView.load(URLRequest(url: exchange))
        }
        session.presentationContextProvider = self
        signInSession = session
        if !session.start() { signInSession = nil }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        view.window ?? ASPresentationAnchor()
    }

    private func open(_ link: String) {
        guard Origins.mayOpenOutside(link), let url = URL(string: link) else { return }
        UIApplication.shared.open(url)
    }

    private func haptic(_ kind: String) {
        switch kind {
        case "success": UINotificationFeedbackGenerator().notificationOccurred(.success)
        case "warn": UINotificationFeedbackGenerator().notificationOccurred(.warning)
        default: UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    private func share(_ text: String) {
        let sheet = UIActivityViewController(activityItems: [text], applicationActivities: nil)
        sheet.popoverPresentationController?.sourceView = view
        present(sheet, animated: true)
    }

    // MARK: navigation

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        // A frame inside the page is the page's own business; the window itself goes only where Origins allows.
        guard navigationAction.targetFrame?.isMainFrame ?? true, let url = navigationAction.request.url else {
            decisionHandler(.allow)
            return
        }
        if Origins.staysInApp(url.absoluteString, cloudURL: cloudURL) {
            decisionHandler(.allow)
            return
        }
        open(url.absoluteString)
        decisionHandler(.cancel)
    }

    /// A link that wants a new window (target=_blank) goes to the system browser, never into a second web view.
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if let url = navigationAction.request.url { open(url.absoluteString) }
        return nil
    }

    /// The starry background turns with the phone; the cloud's own page gets the motion sensors without a second
    /// question (the orientation says nothing about the person), anything else is refused.
    @available(iOS 15.0, *)
    func webView(
        _ webView: WKWebView,
        requestDeviceOrientationAndMotionPermissionFor origin: WKSecurityOrigin,
        initiatedByFrame frame: WKFrameInfo,
        decisionHandler: @escaping (WKPermissionDecision) -> Void
    ) {
        let page = Origins.origin(scheme: origin.protocol, host: origin.host, port: origin.port)
        decisionHandler(page != nil && page == Origins.originOf(cloudURL) ? .grant : .deny)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        offline.isHidden = true
    }

    /// A web view whose process was killed (memory) stays blank: start over from the cloud's page.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        load()
    }

    private func showOffline(_ error: Error) {
        // A navigation the app cancelled itself (a link sent to the system browser) is not a failure.
        if (error as NSError).code == NSURLErrorCancelled { return }
        offline.isHidden = false
    }

    private func makeOfflineView() -> UIView {
        let container = UIView()
        container.backgroundColor = background
        let title = UILabel()
        title.text = NSLocalizedString("offline_title", comment: "")
        title.font = .systemFont(ofSize: 22, weight: .semibold)
        title.textColor = UIColor(white: 0.93, alpha: 1)
        title.textAlignment = .center
        let body = UILabel()
        body.text = NSLocalizedString("offline_body", comment: "")
        body.font = .systemFont(ofSize: 16)
        body.textColor = UIColor(white: 0.64, alpha: 1)
        body.textAlignment = .center
        body.numberOfLines = 0
        var configuration = UIButton.Configuration.filled()
        configuration.title = NSLocalizedString("retry", comment: "")
        configuration.baseBackgroundColor = UIColor(white: 0.95, alpha: 1)
        configuration.baseForegroundColor = background
        let retry = UIButton(configuration: configuration, primaryAction: UIAction { [weak self] _ in self?.load() })
        let stack = UIStackView(arrangedSubviews: [title, body, retry])
        stack.axis = .vertical
        stack.spacing = 12
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: container.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -24),
        ])
        return container
    }
}


extension WKWebView {
    /// The "up, down, Done" strip above the keyboard belongs to forms; a chat has one box, and the strip only makes the
    /// keyboard taller. The web view's content view answers "no accessory" through a subclass made for it.
    func hideKeyboardAccessoryBar() {
        guard let content = scrollView.subviews.first(where: { String(describing: type(of: $0)).hasPrefix("WKContent") }),
              let base = object_getClass(content) else { return }
        let name = "\(NSStringFromClass(base))_NoAccessory"
        var target: AnyClass? = NSClassFromString(name)
        if target == nil, let made = objc_allocateClassPair(base, name, 0) {
            let selector = #selector(getter: UIResponder.inputAccessoryView)
            let nothing: @convention(block) (AnyObject) -> UIView? = { _ in nil }
            if let method = class_getInstanceMethod(base, selector) {
                class_addMethod(made, selector, imp_implementationWithBlock(nothing), method_getTypeEncoding(method))
            }
            objc_registerClassPair(made)
            target = made
        }
        if let target { object_setClass(content, target) }
    }
}
