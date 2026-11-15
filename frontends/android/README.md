# Kvist for Android

A pocket window onto the agent that runs on your computer. **Nothing runs on the phone**: the app finds a
computer on the same router that has Kvist installed, pairs with it by a short code and shows that
computer's sessions: the conversation, what the agent runs, and the requests that wait for you (allow
or deny a command, pick an option). Pixel look, with the squirrel; from 1 December to the end of February
she wears a scarf and it snows.

*(Русская версия: [README.ru.md](README.ru.md))*

## How it works

```text
phone app (WebView, Kotlin)
  1. UDP probe to the subnet broadcast, port 9672  ->  `kvist-web --lan` answers {name, port}
  2. GET http://<computer>:9671/ with the code        ->  200 = paired, 401 = wrong code
  3. opens http://<computer>:9671/m/                  ->  the pixel interface, served by the computer
```

- The interface itself is served by the computer (`frontends/web-local/kvist_web/client/mobile/`), so it is
  always the version that matches the computer's Kvist. The app holds only the connect screen
  (`app/src/main/assets/connect.html`), which shares the stylesheet, sprites and fonts with that page: the
  build copies them from the web client instead of duplicating them.
- It talks to the same HTTP/SSE contract as the browser interface (`frontends/web-local/API.md`).
- The app opens no address but the paired computer. A computer's page cannot reach the app's message channel
  (it is bound to the app's own origin).

## Use it

On the computer (Kvist installed):

```bash
kvist-web --lan          # prints the pairing code; --lan --new-code replaces it
```

In the app the computer appears by itself; tap it and type the code. If the list stays empty (some routers
isolate Wi-Fi clients, or a firewall blocks UDP 9672), use **Enter address** (`192.168.1.20` or
`192.168.1.20:9671`). The computer's firewall must let the phone reach TCP 9671.

**Security.** Whoever is on that network and knows the code drives the agent on the computer, including
commands it asks to run. The traffic is plain HTTP inside the local network; do not use `--lan` on a
network you do not trust. The code is a secret: the app keeps it in private storage and excludes it from
backups. `--new-code` locks every paired phone out.

## Build

Needs JDK 17+ (21 recommended) and the Android SDK (platform 36, build tools).

```bash
cd frontends/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ANDROID_HOME=$HOME/Android/Sdk ./gradlew :app:testDebugUnitTest :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

In an emulator the computer is `10.0.2.2`. `?winter=1` on the connect or `/m/` page shows the winter look in
any month; `?winter=0` turns it off in the season.

## Not done yet

Push notification when the agent waits for you (the app is a live window, it does not run in the background),
several paired computers at once, TLS, a Play Store build and signing.
