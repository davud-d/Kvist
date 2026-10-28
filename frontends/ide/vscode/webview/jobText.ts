import { outputLines } from "../../../tui/ink/format"
import type { JobView } from "../../../tui/ink/state"

// The line of a command's output that says how it went: tools put their verdict last
// ("1 failed, 77 passed in 7.28s"), dressed in rules of "=" or "!".
export function gistOf(output: string): string {
  const lines = outputLines(output).map((line) => line.replace(/^[\s=!#-]+|[\s=!#-]+$/g, "")).filter((line) => line !== "")
  return lines.at(-1) ?? ""
}

export function jobOutcome(status: JobView["status"], exitCode: number | null, russian: boolean): string {
  if (status === "timed_out") return russian ? "Время вышло" : "Timed out"
  if (status === "stopped") return russian ? "Остановлено" : "Stopped"
  if (status === "running") return russian ? "Работает" : "Running"
  return exitCode === 0 ? (russian ? "Готово" : "Done") : (russian ? "Не удалось" : "Failed")
}

// The core's status line names background jobs by id ("job_1"); a person never saw that name.
export function plainStatus(text: string, russian: boolean): string {
  return text.replace(/\bjob_\d+\b/g, russian ? "фоновой команды" : "the background command")
}
