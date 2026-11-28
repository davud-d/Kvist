# Publishing the Kvist Cloud phone apps

*(Русская версия: [cloud-mobile-store.ru.md](cloud-mobile-store.ru.md))*

What the code already does, and what only the owner of the developer accounts can do. The apps are
[`cloud-android`](cloud-android) and [`cloud-ios`](cloud-ios); the store builds come from
[`cloud-mobile-store.yml`](../.github/workflows/cloud-mobile-store.yml), run by hand.

## Done in the code

| Store rule | Where |
| --- | --- |
| No purchases and no pointing to them in a store build (Apple 3.1.3(f), Google Play payments policy) | `KvistHost.store`, [`cloud/mobile`](../cloud/mobile/README.md) |
| Delete the account inside the app (Apple 5.1.1(v), Google Play account deletion) | account sheet; [`account.py`](../cloud/backend/kvist_cloud/account.py), erased after 90 days |
| Sign in with Apple next to GitHub (Apple 4.8) | [`apple.py`](../cloud/backend/kvist_cloud/apple.py), [`AppleSignIn.swift`](cloud-ios/App/AppleSignIn.swift) |
| A privacy policy reachable from the app | `/privacy`, linked in the account sheet |
| Signed, numbered store builds | `cloud-mobile-store.yml` (Play upload key, App Store Connect API key) |

The Google Play merchant program is not open to developers in every country, and Play Billing needs it: the store builds sell
nothing, and plans are bought on the site. Native payment can come later through Freedom Pay.

## Still open

- **A demo account for the reviewers.** Both stores sign in to the app with credentials you give them. The review account
  ([`review.py`](../cloud/backend/kvist_cloud/review.py)) has a plan but opens only from a browser link. The apps need a way in:
  an access code field on the sign-in screen, say.
- Nothing has run on a device or in either store yet; the store workflow has never run.

## Google Play

1. Register in [Play Console](https://play.google.com/console) as an individual ($25, identity check). A new personal account must
   run a **closed test with at least 12 testers for 14 days** before production is open to it.
2. Create the app: name **Kvist Cloud**, package `site.kvist.cloud`, free.
3. Make an upload key: `keytool -genkeypair -v -keystore upload.jks -alias upload -keyalg RSA -keysize 4096 -validity 10000`.
   Keep the file and its passwords outside the repository. Play App Signing keeps Google's own signing key.
4. Repository secrets: `ANDROID_UPLOAD_KEYSTORE_BASE64` (`base64 -w0 upload.jks`), `ANDROID_UPLOAD_KEYSTORE_PASSWORD`,
   `ANDROID_UPLOAD_KEY_ALIAS`, `ANDROID_UPLOAD_KEY_PASSWORD`.
5. Run the workflow (platform `android`), download the `.aab` artifact and upload it **by hand** once (Google's API cannot create
   the first release).
6. For later uploads from the workflow: a Google Cloud service account with access to the app in Play Console (Users and
   permissions), its JSON key in the secret `PLAY_SERVICE_ACCOUNT_JSON`.
7. Store listing, the forms Play asks for:
   - **Data safety:** collected: name, email, user id (account), photos the person attaches, app interactions (sessions);
     purpose: app functionality and account management; encrypted in transit; deletion can be requested in the app.
   - **Account deletion URL:** `https://cloud.kvist.site/m/` (account sheet, Delete account) and the privacy policy.
   - **Content rating** questionnaire (a tool for developers: no violence, no user-to-user chat).
   - **App access:** the demo account (see "Still open").

## App Store

1. Enroll in the [Apple Developer Program](https://developer.apple.com/programs/enroll/) ($99 a year).
2. Certificates, Identifiers and Profiles: App ID `site.kvist.cloud` with **Sign in with Apple** (and Apple Pay only if it is ever
   used); a key with **Sign in with Apple** for the server (`APPLE_TEAM_ID`, `APPLE_KEY_ID`, `.p8`, see
   [`cloud/README.md`](../cloud/README.md#sign-in-with-apple)); a Services ID for the web button, if wanted.
3. App Store Connect: a new app, bundle id `site.kvist.cloud`, name Kvist Cloud, free. Users and Access, Integrations: an **App
   Store Connect API key** with the App Manager role.
4. Repository secrets: `APPLE_TEAM_ID`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8_BASE64` (`base64 -w0 AuthKey_XXXX.p8`).
5. Turn on `APPLE_SIGNIN_ENABLED` on the server, then run the workflow (platform `ios`): it archives the store build, signs it
   with automatic signing and uploads it to TestFlight.
6. App Review information:
   - **Notes:** "Kvist Cloud is the companion app of a paid web service (cloud machines for a coding agent). Plans are bought on
     the website; the app sells nothing and does not link to purchases (Guideline 3.1.3(f)). Sign in with Apple is offered next
     to GitHub. Account deletion: account sheet, Delete account."
   - **Demo account:** see "Still open".
   - **App Privacy:** contact info (name, email), identifiers (user id), user content (photos and messages the person sends),
     usage data; all linked to the account, none used for tracking.
