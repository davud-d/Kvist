# Kvist Cloud for Android

*(Русская версия: [README.ru.md](README.ru.md))*

One WebView with the cloud's phone page ([`cloud/mobile`](../../cloud/mobile)) and the things a page cannot do: the **Google Pay**
sheet (Google's `PaymentsClient`), links in the system browser, the clipboard, a tap's vibration, the back key. No agent runs on
the phone; it is a window onto the cloud.

```bash
cd frontends/cloud-android                      # JDK 21 and ANDROID_HOME with platform 36
./gradlew :app:testDebugUnitTest :app:assembleDebug
# a build that talks to a test server on this computer (the emulator reaches it as 10.0.2.2); plain HTTP is allowed in it only:
./gradlew :app:assembleDebug -PkvistCloudUrl=http://10.0.2.2:8742
```

The product build talks to `https://cloud.kvist.site` and refuses plain HTTP (the CI checks the merged manifest).

## How the page reaches the app

`WebViewCompat.addWebMessageListener` makes a message channel that exists **only for the cloud's own origin**, and a document-start
script ([`Bridge.START_SCRIPT`](app/src/main/java/site/kvist/cloud/Bridge.kt)) turns it into `window.KvistHost`. GitHub's sign-in
page, which loads in the same window, gets nothing. Navigation stays in the app only for the cloud and for GitHub's sign-in
([`Origins`](app/src/main/java/site/kvist/cloud/Origins.kt)); every other link opens in the system browser, and only `http(s)` and
`mailto:` ever leave the app.

## Google Pay

The server decides the price, the merchant and the gateway (`transactionInfo`, `merchantInfo`, a `PAYMENT_GATEWAY` tokenization for
Stripe); the app only opens the sheet with that request and returns `paymentMethodData.tokenizationData.token`
([`GooglePay.kt`](app/src/main/java/site/kvist/cloud/GooglePay.kt)). The environment is the server's word: `TEST` until
`GOOGLE_PAY_ENVIRONMENT=PRODUCTION` and a `GOOGLE_PAY_MERCHANT_ID` are set.

To go live (not done here, it needs your accounts):

1. Register in the [Google Pay & Wallet Console](https://pay.google.com/business/console), get the merchant ID.
2. Publish the app (or a closed test) in Google Play; submit the integration for production access with screenshots of the
   payment flow, as Google's [integration checklist](https://developers.google.com/pay/api/android/guides/test-and-deploy/integration-checklist) asks.
3. Set `GOOGLE_PAY_ENVIRONMENT=PRODUCTION` and `GOOGLE_PAY_MERCHANT_ID` on the server, and the Stripe live keys.

**Play policy.** Google Play requires Google Play Billing for digital goods sold inside an app. Hours of a cloud machine are a
service used from the app, which is a grey area: read the current Payments policy before publishing, or sell outside the app
(the page's other payment methods do that). Installing the APK directly does not have this question.

## What is verified

The Kotlin builds and its 14 unit tests pass (the bridge's messages, the origin rules, Google Pay's JSON). The page's side of the
bridge is exercised by the browser tests with a stand-in host. **Not verified:** the app on a device or an emulator, and the real
Google Pay sheet (it needs a Google account, Play services and, in production, the merchant). The first thing to do with a phone:
open the app, sign in, open a plan and check that the Google Pay button appears and the TEST sheet opens.
