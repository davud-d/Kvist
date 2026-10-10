// Runs the setup screen on the terminal: raw input, the alternate screen, the mouse, and the
// core operations the flow asks for. Resolves with what the person decided.
import { homedir } from "node:os"

import type { CoreCommand } from "../core.js"
import { deleteImage, kittyGraphics, placeRgb, placeRgbAsync } from "../kitty.js"
import { disableMouse, enableMouse } from "../mouse.js"
import { probeTerminal } from "../probe.js"
import { detectColorLevel, type ColorLevel } from "./color.js"
import { artwork } from "./backdrop.js"
import { DRIVES, tildePath } from "./folders.js"
import { initialState, update, type Action, type Effect, type Outcome, type SetupState, type Target } from "./flow.js"
import { parseInput, type InputEvent } from "./keys.js"
import {
  LOGIN_TIMEOUT_MS,
  configOp,
  type ConfigInfo,
  type ListModelsReply,
  type RegistrationReply,
  type SetProviderReply,
} from "./ops.js"
import { PICTURE_Z, pictureSize, renderPicture } from "./picture.js"
import { listDirectory, listDrives, makeDirectory } from "./listing.js"
import { DIMMED, renderScene, sameTarget, type Hit, type Rect } from "./scene.js"
import { detectLocale, stringsFor } from "./strings.js"
import { FramePacer, findClip, findFfmpeg, frameSize, startClip, type Clip } from "./video.js"

// How long a lone ESC waits for the rest of a sequence before it counts as the Escape key.
const ESCAPE_WAIT_MS = 40
const SPINNER_MS = 100

// The picture comes up from black, stays as it is for four seconds so it can be looked at, then
// dims while the panel fades in over it, quickly: an image frame costs tens of milliseconds to
// make and two megabytes to send, so a slow fade only looks like a slow computer. Times are from
// the start, in milliseconds; frames in motion are sent at half size.
const RISE_MS = 300
const REVEAL_AT_MS = 4000
const REVEAL_MS = 250
const FRAME_MS = 50
// A click shows its pressed look for at least this long, so a quick one is still seen.
const PRESS_MIN_MS = 90
const PICTURE_ID = 7
// The demo video is drawn above the cells' backgrounds and below their text, so the blank cells of
// its slot show it and nothing else on the panel is covered.
const VIDEO_ID = 8
const VIDEO_Z = -1
// A frame is dropped when this much is still waiting to be written to the terminal.
const VIDEO_BACKLOG_BYTES = 2_000_000
// How long "Starting Kvist…" stays up before the next interface takes the terminal.
const STARTING_MS = 450

const ease = (t: number) => t * t * (3 - 2 * t)

export function introFrame(elapsed: number): { brightness: number; panel: number; done: boolean } {
  if (elapsed < RISE_MS) return { brightness: 0.25 + 0.75 * ease(elapsed / RISE_MS), panel: 0, done: false }
  if (elapsed < REVEAL_AT_MS) return { brightness: 1, panel: 0, done: false }
  const t = Math.min(1, (elapsed - REVEAL_AT_MS) / REVEAL_MS)
  return { brightness: 1 - (1 - DIMMED) * ease(t), panel: ease(t), done: t >= 1 }
}

export interface SetupOptions {
  command: CoreCommand
  // The folder the agent will work in, shown on the first screen.
  workspace?: string
  // "provider": configure a provider and stop (no folder, no interface).
  scope?: "full" | "provider"
  stdin?: NodeJS.ReadStream
  stdout?: NodeJS.WriteStream
  env?: NodeJS.ProcessEnv
}

// The home folder is written as ~, as a shell prompt would.
export function shortPath(path: string, home: string): string {
  return tildePath(path, home)
}

function hasArtwork(): boolean {
  try {
    artwork()
    return true
  } catch {
    // Anything wrong with the embedded picture means there is none to show.
    return false
  }
}

// What the person decided, and the folder the agent is to work in (the one it started in unless
// they chose another).
export type SetupResult = Outcome & { workspace: string }

export async function runSetup(options: SetupOptions): Promise<SetupResult> {
  const stdin = options.stdin ?? process.stdin
  const stdout = options.stdout ?? process.stdout
  const env = options.env ?? process.env
  const detected: ColorLevel = detectColorLevel(env)
  // A broken bundle must not take the setup with it: without the art there is only the panel.
  const level: ColorLevel = detected !== "none" && hasArtwork() ? detected : "none"
  // Where the terminal can show a real picture, use it: cells are a few thousand pixels. Only
  // terminals known to draw images under the cells' own colours qualify (Konsole does not say).
  const image = level !== "none" && kittyGraphics(env) && !env.KONSOLE_VERSION && env.KVIST_SETUP_PICTURE !== "cells"
  const strings = stringsFor(detectLocale(env))
  const { command } = options
  // HOME is a Unix variable; Windows has USERPROFILE, and homedir() knows either.
  const home = env.HOME ?? env.USERPROFILE ?? homedir()
  const startFolder = options.workspace ?? process.cwd()
  // Nerd Font symbols where the terminal ships them (kitty, Ghostty, WezTerm do), or when asked.
  const icons: "nerd" | "plain" =
    env.KVIST_ICONS === "nerd" || (env.KVIST_ICONS !== "plain" && (kittyGraphics(env) || env.TERM_PROGRAM === "WezTerm")) ? "nerd" : "plain"
  const reducedMotion = Boolean(env.KVIST_REDUCE_MOTION && env.KVIST_REDUCE_MOTION !== "0")
  // The cell size in pixels sizes the image; a terminal that does not say gets an assumed one.
  const cell = image ? (await probeTerminal(stdin, stdout)).cell : null
  // The demo video needs the cell size (to keep its proportions), a decoder and the clip.
  const ffmpeg = image && cell && cell.width > 0 && cell.height > 0 && env.KVIST_SETUP_VIDEO !== "off" ? findFfmpeg(env) : null
  const clip = ffmpeg ? findClip() : null
  const videoCell = ffmpeg && clip ? cell : null

  return new Promise((resolve) => {
    let state: SetupState = initialState(startFolder, home, process.platform === "win32", options.scope ?? "full")
    let hits: Hit[] = []
    let painted: string[] | null = null
    let paintedSize = ""
    let imageShown = ""
    const still = level === "none" || reducedMotion
    let brightness = still ? DIMMED : introFrame(0).brightness
    let panel = still ? 1 : 0
    let intro = !still
    let tick = 0
    let pending = ""
    let escapeTimer: ReturnType<typeof setTimeout> | undefined
    let introTimer: ReturnType<typeof setTimeout> | undefined
    let spinnerTimer: ReturnType<typeof setInterval> | undefined
    let hover: Target | null = null
    let ghost: Rect | null = null
    let lastPanel: Rect | null = null
    let transitioning = false
    let pressed: Target | null = null
    let pressedAt = 0
    let lastPointer: { col: number; row: number } | null = null
    let pointerShape = "default"
    let finished = false
    let connection: AbortController | null = null
    // The running demo video: where it is drawn, the decoder, and the frame on screen.
    let player: { key: string; rect: Rect; width: number; height: number; clip: Clip; pacer: FramePacer } | null = null
    let shownFrame: Buffer | null = null
    // One frame is compressed at a time; a frame that arrives meanwhile is already late.
    let compressing = false

    const sendFrame = (rgb: Buffer, force = false) => {
      const current = player
      if (!current || finished) return
      const started = performance.now()
      if (compressing && !force) return
      if (!force && (!current.pacer.take(started) || stdout.writableLength > VIDEO_BACKLOG_BYTES || (shownFrame && rgb.equals(shownFrame)))) return
      shownFrame = rgb
      const { rect, width, height } = current
      compressing = true
      void placeRgbAsync(rgb, width, height, { id: VIDEO_ID, columns: rect.width, rows: rect.height, row: rect.row, col: rect.col, z: VIDEO_Z }).then((frame) => {
        compressing = false
        // The video may have been stopped or moved while the frame was being compressed.
        if (player !== current || finished) return
        stdout.write(`\x1b[?2026h${frame}\x1b[?2026l`)
        if (!force) current.pacer.record(performance.now() - started)
      })
    }

    const stopVideo = () => {
      if (!player) return
      player.clip.stop()
      player = null
      shownFrame = null
      stdout.write(deleteImage(VIDEO_ID))
    }

    // Keeps the video in step with the screen: playing in `rect` while it is there and the panel is
    // at rest, gone otherwise. `cleared` says a full repaint took the picture off the terminal.
    const syncVideo = (rect: Rect | null, cleared: boolean) => {
      const at = videoCell && clip && ffmpeg && rect && !intro && panel >= 1 && !ghost ? rect : null
      if (!at || !videoCell || !clip || !ffmpeg) return stopVideo()
      const key = `${at.col},${at.row},${at.width},${at.height}`
      if (player?.key === key) return cleared && shownFrame ? sendFrame(shownFrame, true) : undefined
      stopVideo()
      const size = frameSize({ columns: at.width, rows: at.height }, videoCell)
      const started = startClip({ ffmpeg, clip, width: size.width, height: size.height, still: reducedMotion, onFrame: sendFrame })
      player = { key, rect: at, width: size.width, height: size.height, clip: started, pacer: new FramePacer() }
    }

    const paint = () => {
      const cols = stdout.columns ?? 80
      const rows = stdout.rows ?? 24
      const scene = renderScene({ state, strings, cols, rows, level, brightness, panel, tick, workspace: shortPath(state.workspace, home), hover, pressed, icons, ghost, picture: image ? "image" : "cells", video: videoCell })
      hits = scene.hits
      if (!ghost) lastPanel = scene.panel
      const size = `${cols}x${rows}`
      const everything = painted === null || size !== paintedSize
      let out = everything ? "\x1b[2J" : ""
      if (image) {
        // The picture is re-sent only when something in it changed; clearing the screen took it away.
        const pane = scene.panel ? { ...scene.panel, opacity: panel } : null
        const moving = ghost !== null || (intro && !(brightness === 1 && panel === 0))
        const key = `${size}|${moving ? "fast|" : ""}${brightness.toFixed(3)}|${pane ? `${pane.col},${pane.row},${pane.width},${pane.height},${pane.glass},${pane.opacity.toFixed(3)}` : "-"}`
        if (everything || key !== imageShown) {
          const pixels = pictureSize(cols, rows, cell, moving ? 0.5 : 1)
          out += placeRgb(renderPicture(pixels, cols, rows, brightness, pane), pixels.width, pixels.height, { id: PICTURE_ID, columns: cols, rows, z: PICTURE_Z })
          imageShown = key
        }
      }
      scene.rows.forEach((line, row) => {
        if (everything || painted?.[row] !== line) out += `\x1b[${row + 1};1H${line}`
      })
      painted = scene.rows
      paintedSize = size
      if (out) stdout.write(`\x1b[?2026h${out}\x1b[?2026l`)
      syncVideo(scene.video ?? null, everything)
    }

    // A hand over what can be pressed, where the terminal can change the pointer (OSC 22).
    const setPointer = (over: boolean) => {
      if (!image) return
      const shape = over ? "pointer" : "default"
      if (shape === pointerShape) return
      pointerShape = shape
      stdout.write(`\x1b]22;${shape}\x1b\\`)
    }

    const restore = () => {
      clearTimeout(escapeTimer)
      clearTimeout(introTimer)
      clearInterval(spinnerTimer)
      player?.clip.stop()
      player = null
      stdin.off("data", onData)
      stdout.off("resize", onResize)
      if (stdin.isTTY) stdin.setRawMode(false)
      stdin.pause()
      disableMouse()
      stdout.write(`${image ? `\x1b_Ga=d,d=I,i=${PICTURE_ID},q=2\x1b\\${deleteImage(VIDEO_ID)}\x1b]22;default\x1b\\` : ""}\x1b[?2004l\x1b[?25h\x1b[?1049l\x1b[23;0t`)
      process.off("exit", restore)
    }

    const finish = (outcome: Outcome) => {
      if (finished) return
      finished = true
      restore()
      resolve({ ...outcome, workspace: state.workspace })
    }

    const wantsSpinner = () => state.step === "working" || state.step === "starting" || (state.step === "models" && state.models.status === "loading")

    const syncSpinner = () => {
      if (wantsSpinner() && !spinnerTimer) {
        tick = 0
        spinnerTimer = setInterval(() => {
          tick++
          paint()
        }, SPINNER_MS)
      } else if (!wantsSpinner() && spinnerTimer) {
        clearInterval(spinnerTimer)
        spinnerTimer = undefined
      }
    }

    const run = (effect: Effect) => {
      switch (effect.type) {
        case "finish": {
          if (effect.outcome.kind !== "frontend") return finish(effect.outcome)
          // A beat of confirmation: the next screen takes a moment to start, and the window
          // should not go blank in between.
          const outcome = effect.outcome
          state = { ...state, step: "starting" }
          syncSpinner()
          paint()
          return void setTimeout(() => finish(outcome), STARTING_MS)
        }
        case "list":
          return void (effect.path === DRIVES ? listDrives() : listDirectory(effect.path)).then((listing) => dispatch({ type: "listing", listing }))
        case "make":
          return void makeDirectory(effect.parent, effect.name).then((made) => dispatch({ type: "made", path: made.path, error: made.error }))
        case "cancel":
          connection?.abort()
          connection = null
          return syncSpinner()
        case "listModels":
          return void configOp<ListModelsReply>(command, "list-models", { provider: effect.provider, api_key: effect.key }).then((reply) => dispatch({ type: "models", reply }))
        case "connect": {
          const timeout = effect.provider === "chatgpt" ? LOGIN_TIMEOUT_MS : undefined
          connection = new AbortController()
          return void configOp<SetProviderReply>(command, "set-provider", { provider: effect.provider, api_key: effect.key, model: effect.model }, timeout, connection.signal).then((reply) =>
            dispatch({ type: "connected", reply }),
          )
        }
        case "register":
          return void configOp<RegistrationReply>(command, "open-registration", { provider: effect.provider }).then((reply) => dispatch({ type: "registered", reply }))
      }
    }

    const MORPH_FRAMES = [0.3, 0.6, 0.85]
    const MORPH_FRAME_MS = 40

    // Draws an empty pane at sizes between `from` and the one the new screen has, then the screen.
    function morph(from: Rect): void {
      const target = renderScene({ state, strings, cols: stdout.columns ?? 80, rows: stdout.rows ?? 24, level, brightness, panel: 1, tick, workspace: "", hover: null, pressed: null, icons, ghost: null, picture: "cells" }).panel
      if (!target) return paint()
      transitioning = true
      const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t)
      MORPH_FRAMES.forEach((t, index) => {
        setTimeout(() => {
          ghost = { col: lerp(from.col, target.col, t), row: lerp(from.row, target.row, t), width: lerp(from.width, target.width, t), height: lerp(from.height, target.height, t) }
          paint()
        }, index * MORPH_FRAME_MS)
      })
      setTimeout(() => {
        ghost = null
        transitioning = false
        paint()
      }, MORPH_FRAMES.length * MORPH_FRAME_MS)
    }

    function dispatch(action: Action): void {
      if (finished) return
      const before = state.step
      const next = update(state, action, strings)
      state = next.state
      syncSpinner()
      // The dialog and the chooser are different sizes: the pane glides from one to the other.
      if ((before === "folder") !== (state.step === "folder") && lastPanel && !intro) morph(lastPanel)
      else paint()
      // A new screen puts other things under a pointer that has not moved.
      if (state.step !== before && lastPointer) {
        const over = targetAt(lastPointer.col, lastPointer.row)
        if (!sameTarget(hover, over) || (hover === null) !== (over === null)) {
          hover = over
          setPointer(over !== null)
          paint()
        }
      }
      if (next.effect) run(next.effect)
    }

    const endIntro = () => {
      if (!intro) return
      intro = false
      clearTimeout(introTimer)
      brightness = DIMMED
      panel = 1
    }

    const started = Date.now()
    const playIntro = () => {
      if (!intro || finished) return
      const frame = introFrame(Date.now() - started)
      brightness = frame.brightness
      panel = frame.panel
      // The last frame is sent at full size, so it is no longer "in motion" when it is painted.
      if (frame.done) intro = false
      paint()
      if (frame.done) return
      // Nothing changes while the picture rests, so wait for the reveal instead of repainting.
      const elapsed = Date.now() - started
      const wait = frame.panel === 0 && frame.brightness === 1 ? Math.max(FRAME_MS, REVEAL_AT_MS - elapsed) : FRAME_MS
      introTimer = setTimeout(playIntro, wait)
    }

    // The narrowest box under a screen cell: a button inside an option row wins over the row.
    const targetAt = (col: number, row: number): Target | null =>
      hits
        .filter((box) => col >= box.col && col < box.col + box.width && row >= box.row && row < box.row + box.height)
        .sort((a, b) => a.width - b.width)[0]?.target ?? null

    const handle = (event: InputEvent) => {
      // A glide lasts a tenth of a second; keys and clicks that arrive in it would act on a screen not yet drawn.
      if (transitioning && !(event.type === "key" && event.ctrl && event.text === "c")) return
      if (event.type === "mouse") lastPointer = { col: event.col, row: event.row }
      // Only a press ends the intro: the pointer drifting over the window must not cut it short.
      if (intro && event.type === "mouse" && event.kind !== "press") return
      if (intro) {
        // A key before the panel shows only brings it up; once it is visible the key counts.
        const shown = panel > 0
        endIntro()
        if (!shown) return event.type === "key" && event.ctrl && event.text === "c" ? dispatch({ type: "input", event }) : paint()
      }
      if (event.type === "mouse") {
        if (event.kind === "wheel") return dispatch({ type: "wheel", delta: event.delta })
        const target = targetAt(event.col, event.row)
        if (event.kind === "move") {
          if (sameTarget(hover, target) && (hover === null) === (target === null)) return
          hover = target
          setPointer(target !== null)
          return paint()
        }
        if (event.kind === "press") {
          pressed = target
          pressedAt = Date.now()
          return paint()
        }
        // Release: the action happens when the button is let go over the same place it was
        // pressed on, as with any button; letting go elsewhere takes it back.
        const held = pressed
        pressed = null
        if (!held || !sameTarget(held, target)) return paint()
        const wait = Math.max(0, PRESS_MIN_MS - (Date.now() - pressedAt))
        pressed = held
        paint()
        return void setTimeout(() => {
          pressed = null
          hover = null
          dispatch({ type: "click", target: held })
        }, wait)
      }
      dispatch({ type: "input", event })
    }

    const consume = (data: string, final: boolean) => {
      const parsed = parseInput(pending + data, final)
      pending = parsed.rest
      clearTimeout(escapeTimer)
      if (pending) escapeTimer = setTimeout(() => consume("", true), ESCAPE_WAIT_MS)
      for (const event of parsed.events) handle(event)
    }

    function onData(chunk: Buffer | string): void {
      consume(chunk.toString("utf8"), false)
    }
    function onResize(): void {
      painted = null
      paint()
    }

    process.on("exit", restore)
    stdout.write("\x1b[22;0t\x1b]2;Kvist setup\x07\x1b[?1049h\x1b[?25l\x1b[?2004h")
    enableMouse(stdout, false, true)
    if (stdin.isTTY) stdin.setRawMode(true)
    stdin.resume()
    stdin.on("data", onData)
    stdout.on("resize", onResize)

    void configOp<ConfigInfo>(command, "get-config").then((reply) => dispatch({ type: "catalog", reply }))
    if (still) paint()
    else playIntro()
  })
}
