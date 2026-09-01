"use strict"

// Window sizing for the Kvist GUI. The window opens filling the work area with a
// normal frame (minimize/maximize/close, user-resizable) — never fullscreen or
// kiosk, and never restoring stale saved bounds.

const FALLBACK_SIZE = { width: 1180, height: 780 }

// Size the window to the primary display work area up front. On tiling Wayland
// compositors (Hyprland) `win.maximize()` on a floating window is often dropped,
// so starting at work-area size is what actually makes the window open filled;
// `maximize()` on top still gives the proper maximized state everywhere it works.
function buildWindowOptions(screen) {
  const area = primaryWorkArea(screen)
  return {
    width: area.width,
    height: area.height,
    minWidth: 760,
    minHeight: 560,
    backgroundColor: "#080d12",
    title: "Kvist",
    autoHideMenuBar: true,
    resizable: true,
  }
}

function primaryWorkArea(screen) {
  try {
    const size = screen.getPrimaryDisplay().workAreaSize
    if (size && size.width > 0 && size.height > 0) return size
  } catch {
    // screen module unavailable (not ready / headless) — fall through
  }
  return FALLBACK_SIZE
}

// `capture` is true for the offscreen screenshot mode, which must never show or
// maximize the window. Otherwise: show first, then maximize on the next tick —
// a maximize issued before the Wayland surface is mapped is silently dropped.
function applyMaximizeOnShow(win, { capture } = {}) {
  if (capture) return
  win.once("ready-to-show", () => {
    win.show()
    setImmediate(() => {
      if (win.isDestroyed()) return
      win.maximize()
    })
  })
}

module.exports = { buildWindowOptions, primaryWorkArea, applyMaximizeOnShow }
