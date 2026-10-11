// The colour of the spinner line while a turn waits for the model: cyan-blue at first, then
// orange, gold and finally red as the wait grows, always rippling between the warm colour and
// the blue so the change is gradual and never a jump.
const COOL: Rgb = [79, 195, 247]
const COOL_LIGHT: Rgb = [150, 225, 255]
const ORANGE: Rgb = [255, 140, 50]
const GOLD: Rgb = [255, 200, 40]
// A coral red that reads as "late", not as an error, and the deeper red a few minutes in.
const RED: Rgb = [255, 95, 80]
const RED_DEEP: Rgb = [225, 40, 50]

// Seconds of waiting: orange starts to mix in at WARM_FROM and is complete at WARM_TO;
// gold takes over between GOLD_FROM and GOLD_TO, red between RED_FROM and RED_TO and the
// deep red between DEEP_FROM and DEEP_TO.
const WARM_FROM = 1
const WARM_TO = 5
const GOLD_FROM = 5
const GOLD_TO = 25
const RED_FROM = 45
const RED_TO = 100
const DEEP_FROM = 120
const DEEP_TO = 180

// Orange that already shimmers through the blue before the wait grows: the strongest
// share of it at a ripple crest, fading out as the real warming takes over.
const IDLE_ORANGE = 0.4

// One ripple period in milliseconds, and the phase shift per character.
const PERIOD_MS = 2400
const PHASE_PER_CHAR = 0.28

type Rgb = [number, number, number]

function smooth(from: number, to: number, value: number): number {
  const x = Math.min(1, Math.max(0, (value - from) / (to - from)))
  return x * x * (3 - 2 * x)
}

function mix(a: Rgb, b: Rgb, amount: number): Rgb {
  return [0, 1, 2].map((i) => Math.round(a[i]! + (b[i]! - a[i]!) * amount)) as Rgb
}

function hex(rgb: Rgb): string {
  return `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`
}

/**
 * Colour of one character of the spinner line.
 * @param waitingSeconds How long the model has been awaited without an answer.
 * @param nowMs Clock for the ripple.
 * @param index Position of the character, so the ripple travels along the text.
 * @returns A `#rrggbb` string.
 */
export function shimmerColor(waitingSeconds: number, nowMs: number, index: number): string {
  const ripple = 0.5 + 0.5 * Math.sin((2 * Math.PI * nowMs) / PERIOD_MS - index * PHASE_PER_CHAR)
  const warmth = smooth(WARM_FROM, WARM_TO, waitingSeconds)
  const gold = smooth(GOLD_FROM, GOLD_TO, waitingSeconds)
  const cool = mix(COOL, COOL_LIGHT, 0.5 * ripple * (1 - warmth))
  const red = mix(RED, RED_DEEP, smooth(DEEP_FROM, DEEP_TO, waitingSeconds))
  const hot = mix(mix(ORANGE, GOLD, gold), red, smooth(RED_FROM, RED_TO, waitingSeconds))
  // Warming keeps a quarter of the warm colour always present and the ripple brings in the rest;
  // before it starts, only the crests of the ripple turn orange.
  const amount = warmth * (0.25 + 0.75 * ripple) + (1 - warmth) * IDLE_ORANGE * ripple * ripple
  return hex(mix(cool, hot, amount))
}
