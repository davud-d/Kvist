// From 1 January to the end of February the welcome squirrel, once its greeting has played,
// sits in a plaid blanket with a mug and keeps going. The pictures are mascot/frames-winter,
// turned into mascot-winter-*.ts by make_mascot_frames.py --opaque.
const JANUARY = 0
const FEBRUARY = 1

// 130 ms a frame, the pace of the animation the frames were taken from.
export const WINTER_FRAME_MS = 130

// KVIST_WINTER=1 shows it in any month, so it can be checked before the season; =0 turns it off in
// the season, which keeps the tests of the greeting true in January.
export function isWinter(now: Date = new Date(), env: NodeJS.ProcessEnv = process.env): boolean {
  if (env.KVIST_WINTER === "1") return true
  if (env.KVIST_WINTER === "0") return false
  return now.getMonth() === JANUARY || now.getMonth() === FEBRUARY
}
