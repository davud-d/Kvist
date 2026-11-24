# Kvist Cloud for iPhone and iPad

*(Русская версия: [README.ru.md](README.ru.md))*

One `WKWebView` with the cloud's phone page ([`cloud/mobile`](../../cloud/mobile)) and the things a page cannot do: the **Apple Pay**
sheet (PassKit's `PKPaymentAuthorizationController`), links in Safari, the clipboard, haptics. No agent runs on the device; it is a
window onto the cloud.

```bash
cd frontends/cloud-ios
swift test                                       # the core: the bridge, the origin rules, the Apple Pay request (Linux ok)
brew install xcodegen && xcodegen generate       # macOS: makes KvistCloud.xcodeproj
xcodebuild -project KvistCloud.xcodeproj -scheme KvistCloud -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
# a build that talks to a test server on this computer (plain HTTP to local addresses is allowed by App Transport Security):
xcodebuild … KVIST_CLOUD_URL=http://127.0.0.1:8742 build
```

The product build talks to `https://cloud.kvist.site`.

## How the page reaches the app

A `WKUserScript` at document start ([`Bridge.startScript`](Sources/KvistCloudCore/Bridge.swift)) makes `window.KvistHost` out of a
`WKScriptMessageHandlerWithReply`. The handler answers **only a message from the cloud's own origin in the main frame**
([`WebViewController`](App/WebViewController.swift)); GitHub's sign-in page, which loads in the same view, is refused. Navigation
stays in the app only for the cloud and GitHub's sign-in ([`Origins`](Sources/KvistCloudCore/Origins.swift)); every other link goes
to the system browser.

## Apple Pay

The server decides the price, the merchant and the networks; the app builds a `PKPaymentRequest` from its request and returns the
`PKPaymentToken` ([`ApplePay.swift`](App/ApplePay.swift)). The sheet **stays open** until the page says whether the server charged
the token (`payDone`), so the person never sees "Done" for a payment that is then refused.

What it needs, none of which is in this repository:

1. A paid Apple Developer account and a **Merchant ID** (`merchant.site.kvist.cloud` is what
   [`KvistCloud.entitlements`](App/KvistCloud.entitlements) and the server's `APPLE_PAY_MERCHANT_ID` say; change both together).
2. The Merchant ID's **payment processing certificate**: in the Stripe dashboard (Settings, Apple Pay, iOS) create the certificate
   signing request, upload it to Apple, upload the certificate Apple returns to Stripe. Stripe decrypts the token with it.
3. Signing the app with a profile that has the Apple Pay capability (a free Apple ID cannot).

An unsigned build (CI, the simulator without a signed profile) compiles and runs, but `PKPaymentAuthorizationController.canMakePayments`
answers no, so the page shows the other ways to pay.

**App Store review.** Apple requires In-App Purchase for digital goods and services consumed in the app (guideline 3.1.1); Apple
Pay is for physical goods and services outside it. Hours of a cloud machine are a service used from the app, which is a grey
area, and a third-party sign-in (GitHub) needs an equivalent privacy-preserving option under guideline 4.8. Read the current
guidelines before submitting, or distribute outside the store.

## What is verified

The Swift core and its 14 tests pass on Linux. The page's side of the bridge is exercised by the browser tests with a stand-in
host (in WebKit). **Not verified:** the app target ([`App/`](App)) was written without a Mac, so it has never been compiled (the CI
job `ios-app` is the first to do it), and the real Apple Pay sheet needs a device, the merchant and a signed build. Also unverified:
that Stripe accepts the token the way [`wallet.py`](../../cloud/backend/kvist_cloud/wallet.py) hands it over (`POST /v1/tokens` with
`pk_token`, as Stripe's own iOS library does): test it with Stripe's test keys first.
