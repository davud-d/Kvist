# Kvist for Android

A pocket window onto the agent that runs on your computer. **Nothing runs on the phone**: the app lists the
computers on the same router that run Kvist (operating system, host name, user), you tap yours, the computer
asks its owner once, and from then on that phone is trusted: it starts and switches sessions, stops the agent,
answers its requests (allow or deny a command), types `/` commands and changes model, reasoning and role.
Pixel look, with the squirrel; from 1 December to the end of February she wears a scarf and it snows.

*(Русская версия: [README.ru.md](README.ru.md))*

## How it works

```text
computer: any Kvist agent starts  ->  the core brings up `kvist-web --lan` (and stops it a minute after the last agent)
phone app (WebView, Kotlin)
  1. UDP probe to the subnet broadcast, port 9672  ->  the computer answers {id, host name, system, user, port}
  2. tap a computer: POST /pair/request            ->  the computer asks its owner in a window ("Allow this phone?")
  3. owner allows: the phone gets its own token    ->  stored in the app, no code to type
  4. opens http://<computer>:9671/m/ with the token ->  the pixel interface, served by the computer
```

- The interface itself is served by the computer (`frontends/web-local/kvist_web/client/mobile/`), so it is
  always the version that matches the computer's Kvist. The app holds only the connect screen
  (`app/src/main/assets/connect.html`), which shares the stylesheet, sprites and fonts with that page: the
  build copies them from the web client instead of duplicating them.
- It talks to the same HTTP/SSE contract as the browser interface (`frontends/web-local/API.md`).
- The app opens no address but the trusted computer. A computer's page cannot reach the app's message channel
  (it is bound to the app's own origin). When the app starts it reopens the trusted computer by itself, wherever
  the router put it now, and never makes a window appear on the computer unasked.
- The phone sees only the folder(s) where Kvist runs on the computer, and only while it runs there; it cannot
  add a folder or another computer. With one folder there is nothing to choose.
- Works with a computer running Linux, macOS or Windows. The question is a window of that desktop.

## Use it

Start Kvist on the computer as usual. In the app the computer appears by itself; tap it, and allow the phone in
the window that appears on the computer. If the list stays empty (some routers isolate Wi-Fi clients, or a
firewall blocks UDP 9672), use **Enter address** (`192.168.1.20` or `192.168.1.20:9671`). The computer's
firewall must let the phone reach TCP 9671.

**Security.** A trusted phone drives the agent on the computer, commands included, and traffic is plain HTTP
inside the local network. The computer asks before it trusts a phone, and `kvist-web --forget-phones` makes every
phone ask again. The computer is visible on the network only while an agent runs; turn that off altogether with
`KVIST_PHONE=0`. The beacon tells anyone on the network the computer's host name, system and user name.

## Build

Needs JDK 17+ (21 recommended) and the Android SDK (platform 36, build tools).

```bash
cd frontends/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ANDROID_HOME=$HOME/Android/Sdk ./gradlew :app:testDebugUnitTest :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

In an emulator the computer is `10.0.2.2`. `?winter=1` on the connect or `/m/` page shows the winter look in
any month; `?winter=0` turns it off in the season.

## Tablets

The page the computer serves adapts to the window: from 700 × 560 px it shows the sessions beside the conversation and
a desk (waiting request, tool call output, changes), from 1000 px all three at once. The connect screen becomes a
centred column. The same page serves the iPhone/iPad app (`frontends/ios`).

## Not done yet

Push notification when the agent waits for you (the app is a live window, it does not run in the background),
several trusted computers at once, TLS, a Play Store build and signing.
