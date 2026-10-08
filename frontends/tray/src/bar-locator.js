"use strict"

const MAX_BAR_THICKNESS = 120
const EDGE_GAP = 4
const BAR_GAP = 0
const ICON_OFFSET = 28

/**
 * Find the status bar on the display under the pointer.
 *
 * The strip a bar reserves is the difference between a display's `bounds` and
 * its `workArea` (X11 struts, macOS menu bar, Windows taskbar). Hyprland layer
 * surfaces are not in the work area, so `layers` (from `hyprctl layers -j`) is
 * checked first when given. Returns `{edge, bar}` with `bar` a rectangle, or
 * null when nothing is reserved.
 */
function locateBar({ bounds, workArea, cursor, layers = null }) {
  const fromLayer = layers ? barFromHyprlandLayers(layers, cursor, bounds) : null
  return fromLayer || reservedStrip(bounds, workArea)
}

function reservedStrip(bounds, workArea) {
  const strips = [
    { edge: "top", thickness: workArea.y - bounds.y },
    { edge: "bottom", thickness: bounds.y + bounds.height - (workArea.y + workArea.height) },
    { edge: "left", thickness: workArea.x - bounds.x },
    { edge: "right", thickness: bounds.x + bounds.width - (workArea.x + workArea.width) },
  ].filter((strip) => strip.thickness > 0 && strip.thickness <= MAX_BAR_THICKNESS)
  if (!strips.length) return null
  const { edge, thickness } = strips.reduce((best, strip) => (strip.thickness > best.thickness ? strip : best))
  return { edge, bar: stripRect(edge, thickness, bounds) }
}

function stripRect(edge, thickness, bounds) {
  if (edge === "top") return { x: bounds.x, y: bounds.y, width: bounds.width, height: thickness }
  if (edge === "bottom") return { x: bounds.x, y: bounds.y + bounds.height - thickness, width: bounds.width, height: thickness }
  if (edge === "left") return { x: bounds.x, y: bounds.y, width: thickness, height: bounds.height }
  return { x: bounds.x + bounds.width - thickness, y: bounds.y, width: thickness, height: bounds.height }
}

/**
 * `hyprctl layers -j` lists layer surfaces per monitor by level (2 = top, 3 = overlay).
 * The bar is the thin one that holds the pointer: the click that opens the panel happens on it.
 */
function barFromHyprlandLayers(layers, cursor, bounds) {
  for (const monitor of Object.values(layers)) {
    for (const level of ["2", "3"]) {
      for (const layer of (monitor.levels && monitor.levels[level]) || []) {
        const rect = { x: layer.x, y: layer.y, width: layer.w, height: layer.h }
        if (!contains(rect, cursor) || Math.min(rect.width, rect.height) > MAX_BAR_THICKNESS) continue
        return { edge: edgeOf(rect, bounds), bar: rect }
      }
    }
  }
  return null
}

function contains(rect, point) {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height
}

function edgeOf(rect, bounds) {
  if (rect.width >= rect.height) return rect.y + rect.height / 2 < bounds.y + bounds.height / 2 ? "top" : "bottom"
  return rect.x + rect.width / 2 < bounds.x + bounds.width / 2 ? "left" : "right"
}

/**
 * X11 bars (Polybar, i3bar, ...) are dock windows. The icon that was clicked belongs to one,
 * so the dock window under the pointer is the bar; window managers that do not publish a
 * work area (i3) still report it this way. Inputs are the outputs of
 * `xdotool getmouselocation --shell`, `xprop -id ID _NET_WM_WINDOW_TYPE` and
 * `xdotool getwindowgeometry --shell ID`.
 */
function parseX11Dock({ pointer, windowType, geometry, bounds }) {
  if (!windowType.includes("_NET_WM_WINDOW_TYPE_DOCK")) return null
  const field = (text, name) => Number((text.match(new RegExp(`^${name}=(-?\\d+)`, "m")) || [])[1])
  const rect = { x: field(geometry, "X"), y: field(geometry, "Y"), width: field(geometry, "WIDTH"), height: field(geometry, "HEIGHT") }
  if (Object.values(rect).some(Number.isNaN) || !contains(rect, pointer)) return null
  return { edge: edgeOf(rect, bounds), bar: rect }
}

/**
 * Window bounds that attach a panel to the bar under the icon that was clicked.
 * `origin` is the point of the window (relative to it) the panel unfolds from.
 * Without a bar the panel opens at the pointer, inside the display.
 */
function placePanel({ located, cursor, bounds, size }) {
  const clampX = (x) => Math.min(Math.max(x, bounds.x + EDGE_GAP), bounds.x + bounds.width - size.width - EDGE_GAP)
  const clampY = (y) => Math.min(Math.max(y, bounds.y + EDGE_GAP), bounds.y + bounds.height - size.height - EDGE_GAP)
  const edge = located ? located.edge : "top"
  const bar = located ? located.bar : { x: bounds.x, y: bounds.y, width: bounds.width, height: 0 }
  let x
  let y
  if (edge === "top" || edge === "bottom") {
    x = clampX(cursor.x - ICON_OFFSET)
    y = edge === "top" ? bar.y + bar.height + BAR_GAP : bar.y - size.height - BAR_GAP
  } else {
    y = clampY(cursor.y - ICON_OFFSET)
    x = edge === "left" ? bar.x + bar.width + BAR_GAP : bar.x - size.width - BAR_GAP
  }
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: size.width,
    height: size.height,
    origin: {
      x: Math.round(edge === "left" ? 0 : edge === "right" ? size.width : cursor.x - x),
      y: Math.round(edge === "top" ? 0 : edge === "bottom" ? size.height : cursor.y - y),
    },
  }
}

module.exports = { locateBar, placePanel, reservedStrip, barFromHyprlandLayers, parseX11Dock }
