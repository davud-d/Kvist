# Mobile apps and the mobile web

A phone is used with one thumb, in short bursts, while walking, on a slow or
missing network, in sunlight, with notifications competing for attention and
the battery running down. Its owner knows the platform's conventions deeply,
because they use them all day in every other app. Design for those
conditions and follow the platform; a phone app that ignores its platform
feels broken even when it works.

Applies to native apps (SwiftUI, UIKit, Jetpack Compose, Android Views),
cross-platform apps (React Native, Flutter, Kotlin Multiplatform, .NET MAUI)
and to websites and PWAs used on phones.

## Follow the platform

- **iOS**: Apple's Human Interface Guidelines. **Android**: Material Design 3.
  Read the current version for anything specific; both change yearly.
- Cross-platform apps adapt the conventions that people feel most: navigation
  and back behaviour, system controls (date pickers, switches, share sheets,
  alerts), typography, haptics, and where primary actions live. The brand can
  be shared; the behaviour should be native.
- Use system components where they exist: they get accessibility, dynamic
  type, dark mode and future OS changes for free.

| | iOS | Android |
| --- | --- | --- |
| Units | points (pt) | density-independent pixels (dp), text in sp |
| Minimum target | 44×44 pt | 48×48 dp |
| Top-level navigation | tab bar at the bottom, up to 5 tabs | navigation bar at the bottom (3–5 destinations), or a rail/drawer on large screens |
| Back | top-left back button and edge swipe | the system back gesture or button; never trap it |
| Primary action | in the navigation bar or in content | a floating action button or a prominent button in content |
| Transient messages | rarely; banners or inline | snackbars, with an optional action |
| Alerts | `UIAlertController` / `.alert` | Material dialogs |
| Typography | San Francisco, Dynamic Type styles | Roboto or the brand face, Material type scale, sp units |

## Layout and touch

- **Thumb reach**: the bottom half of the screen is easy to reach one-handed,
  the top corners are hard. Put frequent actions low; keep destructive actions
  away from where the thumb rests.
- Targets at least 44 pt / 48 dp, with at least 8 pt / dp between adjacent
  targets. A small icon gets a larger invisible hit area.
- **Safe areas**: content stays clear of the notch, the Dynamic Island, rounded
  corners, the home indicator and the Android gesture bar; backgrounds extend
  edge to edge behind them. On Android 15, apps that target API level 35 are
  drawn edge to edge whether they asked or not, so handle window insets.
- Design for the smallest supported screen first (about 375×667 pt for an
  iPhone SE, 360×640 dp for small Android phones), then larger phones,
  then tablets and foldables (two panes, a navigation rail).
- Support portrait at least; landscape for media, games, reading and tablets.
  Never lose state on rotation.
- One primary task per screen. Long content scrolls vertically; avoid
  horizontal scrolling except for clearly scrollable carousels with a visible
  next item.

## Gestures

- Every gesture has a visible alternative: swipe-to-delete also has an edit
  mode or a menu; pull-to-refresh also has automatic refresh or a button;
  a pinch also has zoom buttons where zoom matters.
- Do not override system gestures (edge swipes for back, the bottom edge for
  home, the top for notifications).
- Long press for secondary actions (context menus), never for the only path
  to something.
- Gestures give immediate feedback (the row follows the finger) and can be
  cancelled by moving back.

## Typography and dynamic type

- Body text 17 pt on iOS, 16 sp on Android; nothing important below 12–13.
- Support the person's text size: Dynamic Type on iOS, font scale on Android
  (sp units scale; dp do not). Test at the largest accessibility sizes:
  layouts must reflow (stack horizontal rows, let labels wrap), not clip.
- Line length on tablets is limited with readable content margins.

## Keyboard and input

- The keyboard type matches the field (email, number pad, phone, URL,
  decimal), the return key says what it does (Next, Search, Send, Done), and
  Next moves to the following field.
- The focused field is never covered by the keyboard: scroll it into view;
  bottom-pinned buttons move above the keyboard.
- Autofill for names, addresses, passwords and one-time codes from SMS
  (`textContentType` / `autofillHints`); allow paste everywhere.
- Avoid typing: pickers, toggles, steppers, defaults, and remembering the
  last value. Typing on a phone is slow and error-prone.
- Validate on submit or on leaving the step, with the message next to the
  field, and keep what was typed.

## Navigation and state

- Shallow hierarchies; a tab keeps its own navigation stack and its scroll
  position when the person switches tabs and comes back.
- The back action always works and never loses unsaved input without asking.
- **State restoration**: the OS kills apps in the background. When the person
  returns, they find the same screen, the same scroll position and their
  half-written text.
- Deep links and universal / app links open the right screen, with a sensible
  back path, including for people who are not signed in yet.
- Modal sheets for focused subtasks, with a clear cancel; a swipe-down dismiss
  asks before discarding input.

## Feedback, performance and battery

- Respond to touch within 100 ms (a highlight, a pressed state); animations
  at the display's refresh rate (60–120 Hz). Jank is felt more on a phone.
- Cold start to a usable screen in under about 2 s; show real structure
  (skeletons of the actual layout) rather than a long splash screen.
- Haptics confirm meaningful moments (success, a toggle, reaching a limit),
  using the system's haptic types; never constant buzzing.
- Network: assume it is slow or absent. Cache what was seen, queue writes and
  send them later (offline-first), show what is stale, never block the UI on
  a request, and retry with backoff.
- Battery and data: no polling in the background without need, large
  downloads on Wi-Fi by default or after asking, images sized for the screen.

## Permissions

- Ask at the moment the feature needs it, after the person has chosen that
  feature: tapping "Scan document" asks for the camera; the first launch does
  not.
- A short explanation before the system prompt helps only when the reason
  is not obvious; the system prompt itself carries your usage description
  string, which must say concretely why ("to scan receipts you photograph").
- Handle refusal and partial grants gracefully: limited photo access on iOS,
  approximate location, "only this time". The feature explains what it needs
  and links to Settings; the rest of the app keeps working.
- Notifications: ask after the person has seen value and in context ("Notify
  me when the build finishes?"), never on first launch.

## Onboarding on mobile

- Let people use the app before signing up whenever possible; when an
  account is required, offer the platform's sign-in (Sign in with Apple,
  Google, passkeys) and keep the form short.
- No multi-page tutorial carousel. Teach in context, the first time a feature
  is used, one tip at a time.
- An empty first screen offers a concrete first action or a sample.
- Ask permissions, ratings and notification consent only after value.
- Everything in `first-run.md` applies, scaled to a small screen and a short
  attention span.

## Accessibility on mobile

- Every control has an accessible label and role (VoiceOver: `accessibilityLabel`,
  traits; TalkBack: `contentDescription`, semantics in Compose; React Native:
  `accessibilityLabel`, `accessibilityRole`; Flutter: `Semantics`).
- Group related elements so a row is read as one item; order matches the
  visual order.
- Contrast as on the web (4.5:1 text, 3:1 non-text), in both themes.
- Respect Reduce Motion, Bold Text, Increase Contrast, Smart Invert (mark
  images and video to not be inverted).
- Test with VoiceOver and TalkBack on a real device for the main task, and
  with the largest text size.

## Dark mode and themes

- Support the system light and dark modes from the start; use semantic
  system colours (`label`, `systemBackground` on iOS; Material colour roles
  and dynamic colour on Android 12+) so the app follows the person's choices.
- Elevation in dark mode by lighter surfaces, as in `visual.md`.

## Mobile web and PWAs

- `<meta name="viewport" content="width=device-width, initial-scale=1,
  viewport-fit=cover">`; never disable zoom.
- Inputs at 16 px or larger, or iOS zooms in on focus.
- `100dvh` (or `svh`/`lvh` deliberately) instead of `100vh`, which ignores the
  browser's toolbars.
- Fixed bottom bars add `env(safe-area-inset-bottom)` padding.
- `touch-action: manipulation` on controls; `:hover` styles that do not trap
  the first tap (use `@media (hover: hover)` for hover-only effects).
- A web app manifest with icons and `theme_color` if it should be installable;
  a service worker for offline when the app is useful offline.

## App store presence

- The store listing is the first screen: screenshots of real screens with a
  short caption each, the main value in the first two, the right device
  frames and sizes for each store.
- The description's first lines say what the app does for the person.
- Privacy labels and data safety forms are accurate; collecting less is the
  best answer.
- Respond to reviews that report real problems; they are free usability
  testing.

## Framework notes

- **SwiftUI / UIKit**: system styles (`.font(.body)`, semantic colours),
  `NavigationStack`, `.sheet`, SF Symbols with text labels where meaning is
  not obvious; test with Dynamic Type and the accessibility inspector.
- **Jetpack Compose**: Material 3 components and theme roles, `Modifier.semantics`,
  window insets (`WindowInsets.safeDrawing`), `rememberSaveable` for state
  that must survive process death.
- **React Native**: `SafeAreaView` / `react-native-safe-area-context`,
  `KeyboardAvoidingView` or a keyboard controller, `Pressable` with
  `hitSlop` for small targets, platform-specific components where behaviour
  differs (`Platform.select`), `FlatList` for long lists.
- **Flutter**: `SafeArea`, `MediaQuery.textScaler`, Material 3 or Cupertino
  widgets chosen per platform where it matters (`.adaptive` constructors),
  `Semantics` for custom widgets.

## Testing on mobile

- Simulators or emulators for the smallest and largest phone, a tablet, and
  at least one real device for touch, performance and screen readers.
- Largest text size, dark mode, landscape, one-handed use, RTL language.
- Airplane mode, a throttled network, switching networks mid-request.
- Interruptions: a call, sending the app to the background for minutes, the
  OS killing it, a low-battery mode, a permission denied.
- First launch on a fresh install, with no account and with an existing one.
