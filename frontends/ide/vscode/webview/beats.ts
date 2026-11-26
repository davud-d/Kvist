// The motion of the cloud/local switch: three beats, a 50 ms start that picks up speed, a 75 ms run that covers most
// of the way, and a 30 ms settle. One curve for the switch's thumb and for the two surfaces, so they move as one.

export const BEATS_MS = [50, 75, 30] as const
export const BEATS_TOTAL_MS = BEATS_MS[0] + BEATS_MS[1] + BEATS_MS[2]

// How far along the way each beat ends, and how each one moves.
const REACHED = [0, 0.2, 0.92, 1]
const EASINGS = ["cubic-bezier(.5, 0, 1, 1)", "linear", "cubic-bezier(0, 0, .3, 1)"]

/**
 * Keyframes for the Web Animations API: `at(progress)` gives the frame for a point of the way (0..1), and every
 * frame but the last carries the easing of the beat that starts there.
 */
export function beatFrames(at: (progress: number) => Keyframe): Keyframe[] {
  let elapsed = 0
  return REACHED.map((progress, index) => {
    const frame: Keyframe = { ...at(progress), offset: elapsed / BEATS_TOTAL_MS }
    if (index < BEATS_MS.length) {
      frame.easing = EASINGS[index]
      elapsed += BEATS_MS[index]
    }
    return frame
  })
}
