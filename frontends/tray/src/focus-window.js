"use strict"

const MAX_ANCESTORS = 16

/**
 * Raise the terminal window that hosts a core.
 *
 * A window is found by process id: the core's ancestors (core, shell, terminal
 * emulator) are tried from the nearest, and the first one that owns a window
 * wins. Compositors and platforms offer different tools, so each backend only
 * says "not found" or throws a message the chat shows to the user.
 *
 * `run(file, args)` resolves to stdout and rejects on a non-zero exit or a
 * missing executable; `platform` and `env` are injected for tests.
 */
async function focusTerminal({ pid, run, platform = process.platform, env = process.env }) {
  const backend = chooseBackend(platform, env)
  if (!backend) {
    throw new Error(`Focusing a terminal window is not supported on ${platform} with this desktop.`)
  }
  const chain = await ancestors(pid, run, platform)
  const method = await backend.focus(chain, run)
  if (!method) {
    throw new Error("Could not find the terminal window for this session.")
  }
  return method
}

function chooseBackend(platform, env) {
  if (platform === "darwin") return { focus: focusMac }
  if (platform === "win32") return { focus: focusWindows }
  if (env.HYPRLAND_INSTANCE_SIGNATURE) return { focus: focusHyprland }
  if (env.XDG_SESSION_TYPE === "wayland" && /kde/i.test(env.XDG_CURRENT_DESKTOP || "")) {
    return { focus: focusKdotool }
  }
  if (env.DISPLAY) return { focus: (chain, run) => focusX11(chain, run, Boolean(env.I3SOCK)) }
  return null
}

async function ancestors(pid, run, platform) {
  if (platform === "win32") return windowsAncestors(pid, run)
  const chain = []
  let current = pid
  while (Number.isInteger(current) && current > 1 && chain.length < MAX_ANCESTORS) {
    chain.push(current)
    const parent = parseInt((await run("ps", ["-o", "ppid=", "-p", String(current)])).trim(), 10)
    if (parent === current) break
    current = parent
  }
  return chain
}

async function windowsAncestors(pid, run) {
  const script = "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"
  const parents = new Map()
  for (const line of (await run("powershell", ["-NoProfile", "-Command", script])).split(/\r?\n/)) {
    const [child, parent] = line.trim().split(" ").map(Number)
    if (Number.isInteger(child)) parents.set(child, parent)
  }
  const chain = []
  for (let current = pid; parents.has(current) && chain.length < MAX_ANCESTORS; current = parents.get(current)) {
    chain.push(current)
  }
  return chain
}

async function focusHyprland(chain, run) {
  const clients = JSON.parse(await run("hyprctl", ["clients", "-j"]))
  for (const pid of chain) {
    const client = clients.find((entry) => entry.pid === pid)
    if (!client) continue
    // focuswindow also switches workspace and monitor.
    await run("hyprctl", ["dispatch", "focuswindow", `address:${client.address}`])
    return "hyprland"
  }
  return null
}

async function focusX11(chain, run, isI3) {
  for (const pid of chain) {
    const found = (await run("xdotool", ["search", "--pid", String(pid)])).trim().split("\n")[0]
    if (!found) continue
    // i3 switches workspace and monitor on `focus`; other window managers on _NET_ACTIVE_WINDOW.
    if (isI3) await run("i3-msg", [`[id=${found}]`, "focus"])
    else await run("xdotool", ["windowactivate", found])
    return isI3 ? "i3" : "x11"
  }
  return null
}

async function focusKdotool(chain, run) {
  for (const pid of chain) {
    const found = (await run("kdotool", ["search", "--pid", String(pid)])).trim().split("\n")[0]
    if (!found) continue
    await run("kdotool", ["windowactivate", found])
    return "kde"
  }
  return null
}

async function focusMac(chain, run) {
  for (const pid of chain) {
    const script =
      `tell application "System Events" to set frontmost of (first process whose unix id is ${pid}) to true`
    try {
      await run("osascript", ["-e", script])
    } catch {
      // The process has no application window (a shell); try its parent.
      continue
    }
    return "macos"
  }
  return null
}

async function focusWindows(chain, run) {
  const script = [
    "Add-Type -Namespace K -Name U -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int c);'",
    `foreach ($p in @(${chain.join(",")})) {`,
    "  $h = (Get-Process -Id $p -ErrorAction SilentlyContinue).MainWindowHandle",
    "  if ($h -and $h -ne 0) { [K.U]::ShowWindow($h, 9) | Out-Null; [K.U]::SetForegroundWindow($h) | Out-Null; 'ok'; break }",
    "}",
  ].join("\n")
  return (await run("powershell", ["-NoProfile", "-Command", script])).includes("ok") ? "windows" : null
}

module.exports = { focusTerminal, chooseBackend, ancestors }
