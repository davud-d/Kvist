# Kvist for iPhone and iPad

A window onto the agent that runs on your computer, the same idea as the Android app. **Nothing runs on the
device**: the app lists the computers on your Wi-Fi that run Kvist, you tap yours, the computer asks its owner
once, and from then on the device is trusted: it starts and switches sessions, stops the agent, answers its
requests (allow or deny a command), types `/` commands and changes model, reasoning and role.

*(Русская версия: [README.ru.md](README.ru.md))*

## One app, two shapes

The app is a thin shell: a web view that shows the page the computer serves at `/m/`
(`frontends/web-local/kvist_web/client/mobile/`). That page chooses its shape from the window it is in, so an
iPad is not a stretched phone:

| Window | What you get |
| --- | --- |
| iPhone, or any window narrower than 700 pt | One screen at a time: the list, then the conversation. Edge swipe goes back. |
| iPad upright, Split View, Stage Manager (700–999 pt wide) | Sessions on the left, the conversation beside them, and a **desk** that slides in from the right. |
| iPad lying down (1000 pt and wider, 560 pt and taller) | Three panes at once: sessions, conversation, desk. |

The **desk** is what a phone has no room for:

- **Needs you** — the request that waits for you (a command or a diff) at full size instead of a sheet over the
  keyboard, and the other sessions that wait, one tap away. A new request takes this tab by itself.
- **Activity** — every tool call of the session, newest first, with its whole output beside the list.
- **Changes** — what the agent changed in the folder (`/diff`), coloured.

With a hardware keyboard: `⌘↵` sends, `⌘K` opens the commands, `Esc` closes them. Pointer hover works.
Rotating the iPad, entering Split View or resizing a Stage Manager window changes the shape without losing the
open session (the session is in the address).

The same layout serves any tablet that opens `/m/`, the Android app included (`tablet.css`, `layout.js`,
`desk.js` next to `pixel.css`).

## How it works

```text
computer: any Kvist agent starts  ->  the core brings up `kvist-web --lan`
iOS app
  1. UDP probe to every address of the Wi-Fi subnet (and its broadcast)  ->  the computer answers {id, host name, system, user, port}
  2. tap a computer: POST /pair/request       ->  the computer asks its owner in a window ("Allow this phone?")
  3. owner allows: the device gets its token  ->  kept in the Keychain (this device only), no code to type
  4. opens http://<computer>:9671/m/ with the token
```

- Discovery differs from Android in one way: iOS lets an app send to a single local address but not to the
  broadcast address without an entitlement Apple grants on request, so the app sends the probe to each address
  of the subnet (at most 1022; a bigger subnet is swept around the device's own address). The beacon on the
  computer answers any private address, so nothing changes there.
- The connect screen is the Android app's own file (`frontends/android/app/src/main/assets/connect.html` and
  `connect.js`), run unchanged: the app provides the same `window.KvistNative` message channel. The stylesheet,
  sprites and fonts come from the web client at build time (`scripts/copy-web-assets.sh`).
- The app opens no address but the trusted computer (`decidePolicyFor`), and only the app's own pages (origin
  `kvist-assets://app`) can use the message channel, never a computer's page.
- `KvistCore/` (a Swift package, Foundation and BSD sockets only) holds everything that is not UIKit: discovery,
  pairing, the rules for reopening a trusted computer, and the message channel's JSON. `App/` holds the web view,
  the Keychain store and the bundled-page scheme handler.
- Works with a computer running Linux, macOS or Windows.

## Build

Needs macOS with Xcode 15+ and [XcodeGen](https://github.com/yonaskolb/XcodeGen).

```bash
cd frontends/ios
brew install xcodegen
xcodegen generate                      # makes Kvist.xcodeproj (not kept in git)
open Kvist.xcodeproj                   # pick a team under Signing, run on a device or a simulator
# or from the command line:
xcodebuild -scheme Kvist -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

On a real device the first search asks for permission to find devices on the local network: allow it.
In a simulator the computer is the Mac itself (`127.0.0.1` by address).

## Without a Mac

The `Mobile Release` workflow (`.github/workflows/mobile-release.yml`) builds the Android APK and an **unsigned** `.ipa` on
its own runners and publishes both to the rolling release `latest-mobile-build` (on pushes to `main`).
Sign and install the `.ipa` with
AltStore, SideStore, Sideloadly or Sideloader and your own Apple ID (a free one lasts 7 days, 3 apps).

## Tests

```bash
cd frontends/ios && swift test         # KvistCore: macOS, or Linux with a Swift 5.9+ toolchain
scripts/live-check.sh                  # the same code against a real kvist-web beacon and pairing server on loopback
```

`swift test` runs on Linux too (a `swift:6.0` container is enough), including a real UDP round trip. The live check
starts the repo's own `kvist_web` beacon and HTTP server (the owner's window replaced by a yes) and drives
scan → pair → reopen with the saved token → "forgotten" → unreachable through the Swift code over real
sockets and `URLSession` (`SWIFT_IN_PODMAN=<image>` runs the Swift side in a container).

## Not done yet

- **Not yet run on a device or simulator.** `App/` was written without Xcode at hand: the WebKit and Keychain code
  has not been compiled, and these assumptions need a real run: ES modules from the `kvist-assets` scheme,
  Basic-auth challenges of the computer's page reaching `didReceive challenge` for sub-resources and the event
  stream, and the local-network permission prompt. CI (`.github/workflows/ios.yml`) builds it on macOS.
- Push notification when the agent waits for you (the app is a live window, it does not run in the background),
  several trusted computers at once, TLS, an App Store build and signing, Bonjour discovery.
- The device's name sent to the computer's question is the model (`iPad`, `iPhone`): iOS 16+ hides the name
  the owner gave the device.
