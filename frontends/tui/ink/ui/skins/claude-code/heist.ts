// One day a year (19 November) the welcome squirrel wears a bandit mask and holds up a car
// key, with the wanted stars and a line beside it. Nothing else changes. The pictures are
// mascot/frames-heist, turned into mascot-heist-*.ts by make_mascot_frames.py.
import { detectLocale, type Locale } from "../../../setup/strings.js"

const NOVEMBER = 10
const DAY = 19

// KVIST_HEIST=1 shows it on any day, so it can be checked before the date.
export function isHeistDay(now: Date = new Date(), env: NodeJS.ProcessEnv = process.env): boolean {
  return env.KVIST_HEIST === "1" || (now.getMonth() === NOVEMBER && now.getDate() === DAY)
}

export const HEIST_STARS = "★★★★★"

const LINES: Record<Locale, string> = {
  en: "Not my keys. Don't ask where I got them.",
  ru: "Ключи не мои. Не спрашивай, откуда.",
}

export function heistLine(env: NodeJS.ProcessEnv = process.env): string {
  return LINES[detectLocale(env)]
}
