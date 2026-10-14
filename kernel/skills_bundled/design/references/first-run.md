# Installation, first run and the first five minutes

The person installing something for the first time knows the least they will
ever know about it, has the least patience they will ever have for it, and
decides in minutes whether to keep it. Every unexplained step, wait, error and
decision on that path loses people who would have been happy users. This page
covers the whole path: finding the install instructions, installing, the
first launch, the first useful result, and later upgrades and removal.

It applies to everything you deliver, not only to products: a script you
wrote for the person, a library, a service, a repository. If someone else (or
the same person in three months) has to run it, its first run is part of the
work.

## 1. The path, measured

Write the path from "I have never seen this" to "it did something useful for
me" as a list of the person's actions: commands typed, clicks, fields filled,
decisions made, waits endured, documents read. Then:

- **Remove** steps rather than explain them. Each decision a safe default can
  make is a decision the person does not have to understand.
- **Move** what cannot be removed to the moment it is needed (a key when the
  first request needs it, a permission when the feature is used, a profile
  when there is something to personalise).
- **Merge** steps that always happen together (create and open; install and
  add to PATH; sign in and pick the default workspace).
- **Explain** only what remains, in the screen where it happens.

Name the first valuable result concretely ("the first test run with a
readable report", "the first answer to a question about their own code") and
route the path straight to it. A feature tour before the person knows why
they would want the features is a delay, not onboarding.

Measure it as described in `process.md` §8: count actions on a clean machine,
screenshot each step, mark every step whose next action is not visible within
five seconds, set a target, change, measure again.

## 2. Getting it onto the machine

### The install command or download

- **One way in per platform**, shown first: one command (`curl -fsSL
  https://… | sh`, `brew install`, `winget install`, `pipx install`, `npm i
  -g`) or one download button that picks the right file for the visitor's OS
  and CPU, with the others one click away.
- **No root unless it is truly needed.** Install into the user's own
  directories (`~/.local/bin`, `~/.local/share/<app>`, `%LOCALAPPDATA%`), and
  ask for elevation only for the one step that needs it, saying why.
- **Say what will happen before it happens**: what will be downloaded, how
  big it is, where it goes, which files outside that folder will change (a
  shell startup file, a desktop entry, an IDE plugin folder). Offer a way to
  read the script first ("download, read, then run").
- **Verify what you download**: a pinned SHA-256 or a signature, checked
  before anything is unpacked. A checksum fetched from the same server as the
  file only proves the download was not corrupted; a pinned value or a
  signature proves more.
- **Idempotent**: running the installer again repairs or upgrades and never
  duplicates (a second identical line in `.bashrc` is a bug). An install that
  fails halfway leaves the previous version working.
- **Downloads behave**: show size and progress, give up on a stalled
  connection after a sensible time rather than hanging forever, try mirrors
  when the main server is slow, and say which step failed with what to do.
- **Optional parts are optional**: a failure of an extra (an IDE plugin, a
  codec, shell completions) is a warning, and the core install continues.
  Let the person choose extras, with the core always included and the
  recommended ones preselected.
- **Detect, do not ask, where you can**: OS, CPU architecture, shell, package
  manager, installed IDEs. Show what was detected so a wrong guess is visible.
- **End with the next step**: the last lines say it worked, what was
  installed where, and the exact command to run now. If the PATH changed only
  for new shells, say so and give the command that works in the current one.
- **Non-interactive mode**: flags or environment variables for every choice,
  so CI, Docker and provisioning tools can install without a TTY. Detect the
  missing TTY and do not prompt.
- **Platform specifics**:
  - macOS: browser downloads are quarantined by Gatekeeper; an unsigned app
    shows a frightening dialog. Sign and notarise if you can; otherwise say
    exactly how to open it. `curl` downloads are not quarantined. Ship both
    arm64 and x86-64 (or universal) builds. The system bash is 3.2.
  - Windows: an unsigned installer triggers SmartScreen. Prefer per-user
    installs that need no UAC prompt. Mind spaces in paths
    (`C:\Program Files`, user names with spaces), long paths, and that PATH
    changes reach only newly started programs.
  - Linux: many distributions; prefer a static binary or a format that does
    not depend on the system's library versions. Name the system packages a
    feature needs per distribution (`apt`, `dnf`, `pacman`, `zypper`) when
    you cannot install them yourself.
- **Proxies and offline**: respect `HTTPS_PROXY` and `NO_PROXY`; allow an
  offline install from a downloaded bundle; never require a network call to an
  analytics service to finish installing.

### Package managers and libraries

- The first code sample in the README runs as pasted, with the import line,
  in the language's most common setup. Show the output it produces.
- State supported versions (runtime, OS) near the install line, not on a
  separate page.
- Pin nothing the person did not ask for; ship sane defaults so the shortest
  example is also a correct one.

### Uninstall

- Documented next to the install instructions, one command or one button,
  and it removes everything install added: binaries, desktop entries, shell
  lines it inserted (exactly those), services, caches. Ask before deleting the
  person's data (configs, history, projects) and say where that data lives.
- Leaving things behind after uninstall is the last impression people take
  away (peak-end rule).

## 3. The first launch

### Start working, ask little

- **It works with defaults.** Ask only what the program cannot know and
  cannot default: usually one credential and, for some tools, a folder.
- **One decision per screen**, the recommended option first and preselected,
  each option described by its consequence for the person ("Faster, may need
  a second try"), not by its internal name.
- **Never ask for what you can detect** (language from the locale, theme
  from the system, the project type from its files), but show what you
  detected so it can be corrected.
- **Show where it is working**: the folder, account, project or environment
  is on the first screen. Starting in the home directory or the wrong project
  is the most expensive first-run mistake. If the chosen folder is very broad
  (the home folder, a disk root, Desktop or Downloads), say plainly what the
  program could read and change there and make "choose another folder" the
  default answer.
- **Defer sign-up and sign-in** until the person has seen value, when the
  product allows it. When it does not (an API key is required), make getting
  the key part of the flow: a link that opens the right page, and a field
  that accepts the result.
- **Permissions in context**: request camera, notifications, file access,
  network access at the moment the feature that needs them is used, with one
  sentence on why. A permission asked up front without context is usually
  refused, and the refusal is hard to undo.
- **Respect the person's time**: a splash screen or intro animation that
  cannot be skipped is a toll. Keep any intro to a second or two, make a key
  or click skip it, never replay it on every launch.
- **No dead ends**: every screen has a way forward and a way back (Esc, Back),
  and quitting during setup saves nothing half-done and says so.

### Credentials and keys

- Accept the key the way people actually paste it: trim spaces and newlines,
  strip surrounding quotes and a `Bearer ` prefix, accept it with or without
  the vendor's prefix where that is unambiguous.
- Mask it while typing or after paste, but let the person reveal it (a
  show/hide toggle, or the last four characters always visible) so they can
  check they pasted the right one.
- **Validate immediately** with a real but cheap request, show progress after
  a short delay, and on failure say which of the likely causes it is (wrong
  key, no network, the service's own error message, a proxy) and what to do.
  A known prefix mismatch (the key looks like it belongs to another service)
  is a quiet warning, never a block: prefixes change.
- Let the person cancel a check in progress; ignore its late answer.
- Store secrets where secrets belong (the OS keychain, or a file only the
  user can read), never in a project file that might be committed, never in
  logs, never in a URL. Say where it was stored.
- Never echo a secret back in full in any output, error or debug log.

### The first screen after setup

- Not blank. Offer what fits what the person has: three concrete first
  actions chosen from the context ("Explain this project", "Find failing
  tests" for a code folder; "Create a project" for an empty one), a sample
  project, a template gallery, or an example already loaded and clearly
  marked as an example.
- The main input is focused and its placeholder shows a real example.
- The help that matters (how to get help, the one shortcut that matters,
  where settings live) is visible without opening anything, and gets out of
  the way once used.
- Empty states teach: say what will appear here, why it is empty, and the
  one action that fills it.
- Checklists ("3 of 5 done") work for products with a genuine multi-step
  setup; they are noise for a tool that is ready after one step.

### When things go wrong on the first run

First-run errors are the most damaging errors a product has, because the
person has no reason yet to believe the product works.

- Catch the common ones and say what to do: no network, a proxy, a missing
  system dependency (name the package and the install command for their
  system), unsupported OS version, no permission to write the target folder,
  a port already in use, an old version of a runtime.
- Never lose what the person entered; going back keeps every answer.
- Show the program's own message and the underlying cause, with the cause
  collapsed or in a log if it is long.
- Make "try again" one action, and make a retry safe.
- An interrupted setup resumes where it stopped on the next launch, or
  restarts cleanly; it never leaves a half-written configuration that makes
  every later launch fail.

### Small things people notice

- The working folder, the account and the version are visible somewhere on
  the first screen.
- A wait over a few seconds says how long it has lasted ("Connecting… 8 s"),
  and Esc cancels it.
- The step that finishes setup confirms it with the specifics ("Connected to
  DeepSeek, model deepseek-chat") and goes straight to the product, with a
  short hold so the screen does not flash blank between two views.
- The terminal or window title says what it is.
- The first run respects reduced-motion and no-colour settings already.
- Copy is in the person's language from the first screen (detect from the
  locale, let them switch).

## 4. The README and the landing page are part of the product

- The first screen of the README answers: what is this, who is it for, what
  does it look like (a screenshot or a short recording of the real product),
  how do I install it, what do I type first.
- The quickstart is copy-pasteable, works on a clean machine, and shows the
  expected output. Test it on one (a container, a fresh user, a new
  `HOME`-like config directory where the product supports one).
- Requirements are listed before the install command, not discovered after
  it fails.
- One canonical path first; alternatives (other package managers, building
  from source) after it.
- Troubleshooting for the three most common first-run failures, with their
  exact error messages so people can find them by searching.
- Uninstall at the end of the install section.

## 5. Upgrades and migrations

- Upgrading is one command or one click, and preserves settings, data,
  sessions and credentials.
- Old configuration and data files are read by the new version and migrated
  forward, with a backup kept; a migration never deletes the previous
  generation until the new one is proven.
- Breaking changes are announced in the product before they happen, with the
  replacement and how to migrate; after the upgrade, a short "what changed
  for you" note, once, dismissible.
- Never reset the person's choices (theme, model, layout, shortcuts) on
  upgrade.
- Downgrade or rollback is possible, or at least the backup is.

## 6. Telemetry and trust

- Nothing leaves the machine without the person knowing: say what is sent,
  where, why and how to turn it off, on first run and in the docs. Prefer
  opt-in.
- A first run that reaches out to the network says so (license checks,
  update checks, model catalogs) and still works, degraded, when it fails.
- Say what the product will not do (read secret files, run commands without
  asking, send code anywhere), in one line, on the first screen where that
  worry is likely. Trust is part of the first impression.

## 7. Testing a first run like a stranger

- **Clean environment**: a container or VM, or at least a fresh user account
  or an isolated config directory. Do not test with your own configured
  machine: it already has the credentials, caches and tools a stranger lacks.
  When the product opens a browser, an isolated `HOME` also gives the browser
  an empty profile and may change which browser opens; isolate the product's
  own config directory instead where it supports that.
- **Missing tools**: no Node, no Python venv, no compiler, no `git`, no
  `ffmpeg`, whichever the product touches. Every missing tool gives a message
  that names it and how to get it.
- **Hostile conditions**: slow network (throttle), no network, a proxy, a
  non-English locale (`LANG=ru_RU.UTF-8`), a non-UTF-8 locale, a path with
  spaces and Cyrillic letters, a small terminal or window, a screen reader for
  the first screen, a light and a dark theme.
- **Interruptions**: Ctrl+C at each step; close the window during setup;
  kill the network during a download; run the installer twice.
- **Record the path**: screenshots or terminal captures of each step, and the
  action count, before and after your change.

What you can do yourself in a Kvist sandbox, and what you cannot:

- A command-line first run: start the program with an empty config directory
  (its own variable such as `XDG_CONFIG_HOME` or `<APP>_CONFIG_DIR`, pointed at
  a new folder in the workspace) through the terminal harness in
  `terminal.md`, answer its prompts with steps, and read every screen. Ctrl+C
  at each step is one more run each.
- A web first run: the browser skill against the dev server, at a phone and a
  laptop size (`web-apps.md`).
- Missing network: commands run without network unless they ask for it, which
  is itself a useful test of the offline path.
- Not possible here: a real fresh machine, another OS, a real screen reader,
  a light terminal theme. Name these in the report as checks for the person.

## 8. A first-run checklist

- [ ] One install command or download per platform, shown first.
- [ ] No root unless needed, with the reason; per-user install by default.
- [ ] What will be installed and where is said before installing.
- [ ] Downloads verified; a failed install leaves the old version working.
- [ ] A second run repairs and does not duplicate.
- [ ] The installer ends with the exact next command.
- [ ] Works without a TTY with flags; no prompt in CI.
- [ ] First launch works with defaults; asks only what it cannot know.
- [ ] The working folder/account is visible; broad folders are questioned.
- [ ] Keys accepted as pasted, validated at once, stored safely, never echoed.
- [ ] Permissions asked in context, with a reason.
- [ ] The first screen offers concrete first actions; nothing is blank.
- [ ] Common first-run failures have specific messages with the fix.
- [ ] Interrupted setup resumes or restarts cleanly.
- [ ] Intro, if any, is short and skippable; reduced motion respected.
- [ ] README quickstart tested on a clean machine with expected output shown.
- [ ] Uninstall documented and complete; user data kept unless they agree.
- [ ] Upgrades keep settings and data; migrations keep a backup.
- [ ] No telemetry without consent; network use is stated.
