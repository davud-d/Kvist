# Kvist Cloud for iPhone and iPad

*(Русская версия: [README.ru.md](README.ru.md))*

One `WKWebView` with the cloud's phone page ([`cloud/mobile`](../../cloud/mobile)) and the things a page cannot do: the **Apple Pay**
sheet (PassKit's `PKPaymentAuthorizationController`), links in Safari, the clipboard, haptics. Photos come through the page itself: the web view opens the
library and the camera for its "+" (the camera needs `NSCameraUsageDescription`, in `project.yml`), and WebKit hands a photo dragged
from another app (iPad split view, iPhone between apps) to the page as an ordinary drop. No agent runs on the device; it is a window
onto the cloud.

```bash
cd frontends/cloud-ios
swift test                                       # the core: the bridge, the origin rules, the Apple Pay request (Linux ok)
brew install xcodegen && xcodegen generate       # macOS: makes KvistCloud.xcodeproj
xcodebuild -project KvistCloud.xcodeproj -scheme KvistCloud -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
# a build that talks to a test server on this computer (plain HTTP to local addresses is allowed by App Transport Security):
xcodebuild … KVIST_CLOUD_URL=http://127.0.0.1:8742 build
# the App Store build: the page sells nothing in it (see cloud/mobile/README.md):
xcodebuild … KVIST_STORE=YES build
```

The product build talks to `https://cloud.kvist.site`.

## Try it on your iPhone (no paid account)

Use the file `Kvist-Cloud-ios-unsigned.ipa` from the GitHub release **"Kvist Cloud phone apps (rolling)"** (built by
[`cloud-mobile-release.yml`](../../.github/workflows/cloud-mobile-release.yml); a manual run lets you choose the cloud address). It is
unsigned: a sideloading tool signs it with your own Apple ID. **Apple Pay does not work in this build** (the entitlement needs a paid
developer account and a Merchant ID); sign-in, the chat, plans and the other ways to pay do.

1. The cloud at the address in the release notes must already run this version, or the app says "No connection".
2. On the iPhone: Settings, Privacy & Security, **Developer Mode** on (iOS 16+; it restarts the phone).
3. On a computer, install a sideloading tool: [AltStore](https://altstore.io) (macOS and Windows; AltServer sits in the tray and
   installs over Wi-Fi or a cable), [Sideloadly](https://sideloadly.io) (macOS and Windows), or on Linux a community tool such as
   AltServer-Linux or Sideloader. I could not test any of them: follow the tool's own instructions for "install an .ipa".
4. Give it the `.ipa` and your Apple ID (a throwaway one is fine; the tool asks for an app-specific password if it has two-factor).
5. On the iPhone: Settings, General, **VPN & Device Management**, your Apple ID, **Trust**. Open Kvist Cloud.
6. The free Apple ID signature lasts **7 days** and allows 3 apps: reinstall (or let AltStore refresh over Wi-Fi) when it expires.

What to check, in order: the sign-in screen; "Continue with GitHub" and back to the app; accepting the terms; the plans sheet (prices);
a session (send a message, watch the work line fold); the keyboard (the composer stays above it and the last message is not hidden);
rotating is off on iPhone; the drawer; Russian (account sheet, language); "pay another way". If something is off, send a screenshot
and what you tapped.

**With a paid developer account** (Apple Pay): make the Merchant ID `merchant.site.kvist.cloud` (or change it in
[`KvistCloud.entitlements`](App/KvistCloud.entitlements) and the server's `APPLE_PAY_MERCHANT_ID`), `brew install xcodegen`,
`xcodegen generate`, open `KvistCloud.xcodeproj`, choose your team under Signing, run on the phone. The wallet side also needs
`WALLET_ENABLED` on the server with Stripe test keys (see [`cloud/README.md`](../../cloud/README.md)).

## Signing in

GitHub's page, and Google's behind "sign in with Google", will not run in a web view, and if they were opened in Safari the
session would stay in Safari. So the app signs in through the system browser's own session (`ASWebAuthenticationSession`, which
shares Safari's cookies) and the cloud hands the app a short-lived signed code (`/auth/app/start`, `kvistcloud://signed-in?code=`);
the web view trades it, with a random nonce only this app knows, for its own session (`/auth/app/exchange`). See
[`appauth.py`](../../cloud/backend/kvist_cloud/appauth.py) and [`AppSignIn.swift`](Sources/KvistCloudCore/AppSignIn.swift).

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
