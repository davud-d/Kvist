// Plays the Claude-style demo clip on the interface step. The terminal has no video of its own, so
// the user's ffmpeg decodes the clip to raw RGB at the size of the rectangle it is shown in and the
// setup screen sends each frame as a terminal image (kitty graphics, see run.ts). Without ffmpeg
// or without the clip beside the bundle there is no video and the step keeps its text preview.
import { spawn } from "node:child_process"
import { accessSync, constants } from "node:fs"
import { homedir } from "node:os"
import { delimiter, join } from "node:path"
import { fileURLToPath } from "node:url"

// The clip is 16:9 (demo/make_demo.py).
const ASPECT = 16 / 9
// Below this many rows the picture is too small to read the terminal text in it.
export const MIN_VIDEO_ROWS = 8
const MAX_VIDEO_ROWS = 18
// Frames wider than this are decoded at this width and scaled up by the terminal: each frame is
// width x height x 3 bytes before compression, and up to 60 of them go out every second.
const MAX_FRAME_WIDTH = 960
// The clip's own rate: motion is smooth at 60, and the pacer below drops to 30 when sending
// frames costs more than the machine or the terminal link can spare.
export const VIDEO_FPS = 60
// The frame shown when motion is off (a transcript with the agent writing a README).
const POSTER_SECONDS = 28

export interface CellSize {
  width: number
  height: number
}

export interface VideoFit {
  columns: number
  rows: number
}

// The largest 16:9 rectangle of cells that fits `inner` columns and `maxRows` rows, judged by the
// terminal's own cell proportions; null when it would be too small to be worth showing.
export function videoFit(inner: number, maxRows: number, cell: CellSize): VideoFit | null {
  let rows = Math.min(maxRows, MAX_VIDEO_ROWS)
  if (rows < MIN_VIDEO_ROWS) return null
  let columns = Math.round((rows * cell.height * ASPECT) / cell.width)
  if (columns > inner) {
    columns = inner
    rows = Math.floor((columns * cell.width) / ASPECT / cell.height)
  }
  return rows < MIN_VIDEO_ROWS ? null : { columns, rows }
}

// Pixel size of the frames that fill `fit`, never wider than MAX_FRAME_WIDTH.
export function frameSize(fit: VideoFit, cell: CellSize): { width: number; height: number } {
  const width = Math.min(MAX_FRAME_WIDTH, fit.columns * cell.width)
  return { width, height: Math.round((width * fit.rows * cell.height) / (fit.columns * cell.width)) }
}

function executable(path: string): boolean {
  try {
    accessSync(path, constants.X_OK)
    return true
  } catch {
    // Not there, or not runnable: the next candidate is tried.
    return false
  }
}

// The ffmpeg to decode with: KVIST_FFMPEG when it names a runnable file, else the first one on PATH,
// else the one the release installer unpacked beside kvist-core (~/.local/share/kvist/ffmpeg).
export function findFfmpeg(env: NodeJS.ProcessEnv, platform: NodeJS.Platform = process.platform, home: string = homedir()): string | null {
  if (env.KVIST_FFMPEG) return executable(env.KVIST_FFMPEG) ? env.KVIST_FFMPEG : null
  const name = platform === "win32" ? "ffmpeg.exe" : "ffmpeg"
  for (const directory of (env.PATH ?? "").split(delimiter)) {
    if (directory && executable(join(directory, name))) return join(directory, name)
  }
  const installed = join(home, ".local", "share", "kvist", "ffmpeg", name)
  return executable(installed) ? installed : null
}

// The clip sits beside the bundle (dist-ui/), or in demo/ when running from source.
export function findClip(): string | null {
  const candidates = [new URL("./claude-style.mp4", import.meta.url), new URL("../demo/claude-style.mp4", import.meta.url)]
  for (const candidate of candidates) {
    const path = fileURLToPath(candidate)
    try {
      accessSync(path, constants.R_OK)
      return path
    } catch {
      // Not at this location: the next one is tried.
    }
  }
  return null
}

// Cuts a byte stream into whole frames and keeps only the newest: a frame that arrives while the
// previous one is still being sent is already late.
export class FrameReader {
  private pending: Buffer = Buffer.alloc(0)

  constructor(private readonly frameBytes: number) {}

  push(chunk: Buffer): Buffer | null {
    this.pending = this.pending.length === 0 ? chunk : Buffer.concat([this.pending, chunk])
    const whole = Math.floor(this.pending.length / this.frameBytes)
    if (whole === 0) return null
    const frame = this.pending.subarray((whole - 1) * this.frameBytes, whole * this.frameBytes)
    this.pending = this.pending.subarray(whole * this.frameBytes)
    return frame
  }
}

// Decides which decoded frames are sent. Every frame goes out (60 fps) until the time from taking
// a frame to the terminal having its data averages more than `SLOW_SEND_MS`, which is nearly a
// whole 60 fps interval; from then on frames go out at least `FALLBACK_INTERVAL_MS` apart (30 fps,
// with room for the jitter of frames arriving). It does not go back up: the cost of a frame changes
// only when the picture's size does, and a new player starts a new pacer.
export const SLOW_SEND_MS = 14
export const FALLBACK_INTERVAL_MS = 30
const SMOOTHING = 0.9
const WARMUP_FRAMES = 20

export class FramePacer {
  private interval = 0
  private last = Number.NEGATIVE_INFINITY
  private average = 0
  private sent = 0

  // Whether a frame that is ready at `now` (milliseconds, any steady clock) is to be sent.
  take(now: number): boolean {
    if (now - this.last < this.interval) return false
    this.last = now
    return true
  }

  // Reports what sending one frame took, in milliseconds.
  record(milliseconds: number): void {
    // The first frames pay for cold code and the decoder starting up; they are not the steady cost.
    if (this.sent++ < WARMUP_FRAMES) return
    this.average = this.sent === WARMUP_FRAMES + 1 ? milliseconds : SMOOTHING * this.average + (1 - SMOOTHING) * milliseconds
    if (this.interval === 0 && this.sent >= WARMUP_FRAMES + 10 && this.average > SLOW_SEND_MS) this.interval = FALLBACK_INTERVAL_MS
  }

  get fps(): number {
    return this.interval === 0 ? VIDEO_FPS : VIDEO_FPS / 2
  }
}

export interface ClipOptions {
  ffmpeg: string
  clip: string
  width: number
  height: number
  // One frame at the poster time instead of the looping video.
  still: boolean
  onFrame: (rgb: Buffer) => void
}

export interface Clip {
  stop: () => void
}

// Decodes the clip, looping, at its own speed (-re), to `width` x `height` RGB frames.
export function startClip(options: ClipOptions): Clip {
  const { width, height } = options
  const input = options.still ? ["-ss", String(POSTER_SECONDS)] : ["-re", "-stream_loop", "-1"]
  const output = options.still ? ["-frames:v", "1"] : []
  const args = [
    ...["-v", "error", "-nostdin"],
    ...input,
    ...["-i", options.clip, "-an", "-vf", `fps=${VIDEO_FPS},scale=${width}:${height}:flags=bicubic`],
    ...["-f", "rawvideo", "-pix_fmt", "rgb24"],
    ...output,
    "pipe:1",
  ]
  const process = spawn(options.ffmpeg, args, { stdio: ["ignore", "pipe", "ignore"] })
  const reader = new FrameReader(width * height * 3)
  let stopped = false
  process.stdout.on("data", (chunk: Buffer) => {
    const frame = reader.push(chunk)
    if (frame && !stopped) options.onFrame(frame)
  })
  // A decoder that cannot start (permissions, a wrong binary) leaves the last picture as it is.
  process.on("error", () => {
    stopped = true
  })
  return {
    stop: () => {
      stopped = true
      process.kill("SIGKILL")
    },
  }
}
