export type Tone = "ok" | "warn" | "bad"

interface FinishedRun {
  ok: boolean
  exit_code: number | null
  output: string
}

// "0 errors" and "no warnings" report success, so they must not read as trouble.
const CLEAN_COUNTS = /\b(?:0|no|zero)\s+(?:errors?|warnings?|failed|failures?)\b|\b(?:errors?|warnings?|failed|failures?)\s*[:=]\s*0\b/gi

// Capital ERROR and FAILED are what tools print for themselves; ordinary prose that
// merely mentions "error handling" is not. A lowercase word counts only at the
// start of a line, where compilers and linters put their labels.
const ERROR_LABEL = /\b(?:ERROR|FATAL|FAILED|FAIL)\b/
const ERROR_LINE = /^\s*(?:error|fatal)\b[:\s]/im
const TRACEBACK = /Traceback \(most recent call last\)/
const WARNING_LABEL = /\b(?:WARN|WARNING)\b/
const WARNING_LINE = /^\s*warning\b[:\s]/im
const DEPRECATION = /\bdeprecat/i
// A summary line with a real number: "3 errors", "2 warnings", "failed: 1".
const ERROR_COUNT = /\b[1-9]\d*\s+(?:errors?|failed|failures?)\b|\b(?:errors?|failed|failures?)\s*[:=]\s*[1-9]/i
const WARNING_COUNT = /\b[1-9]\d*\s+warnings?\b|\bwarnings?\s*[:=]\s*[1-9]/i

const SEVERITY: Record<Tone, number> = { ok: 0, warn: 1, bad: 2 }

export function worseOf(a: Tone, b: Tone): Tone {
  return SEVERITY[b] > SEVERITY[a] ? b : a
}

// How a finished command should read at a glance: red for a failure, yellow for a
// warning, green otherwise. The exit code decides first; the output can still turn
// a clean exit yellow or red, because many tools report problems and exit 0.
export function classifyRun(run: FinishedRun): Tone {
  if (!run.ok || (run.exit_code !== null && run.exit_code !== 0)) return "bad"
  const text = run.output.replace(CLEAN_COUNTS, "")
  if (ERROR_LABEL.test(text) || ERROR_LINE.test(text) || TRACEBACK.test(text) || ERROR_COUNT.test(text)) return "bad"
  if (WARNING_LABEL.test(text) || WARNING_LINE.test(text) || DEPRECATION.test(text) || WARNING_COUNT.test(text)) return "warn"
  return "ok"
}
