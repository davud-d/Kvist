import { formatSeconds, outputLines } from "../../../tui/ink/format"
import type { JobView } from "../../../tui/ink/state"

type JobStatus = JobView["status"]

const COPY = {
  en: {
    running: "Running in the background",
    background: "background",
    noOutput: "No output yet",
    earlier: (count: number) => `… ${count} earlier lines`,
    more: (count: number) => `+${count} more`,
    told: "The model was told:",
    ended: (status: JobStatus, exitCode: number | null) => {
      if (status === "timed_out") return "timed out"
      if (status === "stopped") return "stopped"
      return `exit ${exitCode ?? "?"}`
    },
  },
  ru: {
    running: "Работает в фоне",
    background: "в фоне",
    noOutput: "Пока нет вывода",
    earlier: (count: number) => `… ещё ${count} строк выше`,
    more: (count: number) => `ещё ${count}`,
    told: "Модель получила:",
    ended: (status: JobStatus, exitCode: number | null) => {
      if (status === "timed_out") return "время вышло"
      if (status === "stopped") return "остановлена"
      return `код ${exitCode ?? "?"}`
    },
  },
}

export function jobsCopy(russian: boolean) {
  return russian ? COPY.ru : COPY.en
}

// The purpose the model gave, else the command itself.
export function jobLabel(job: { purpose: string; command: string[] }): string {
  return job.purpose || job.command.join(" ") || "job"
}

// A stopped job is the person's or the runtime's decision, so only a failed exit or a timeout is red.
export function jobFailed(status: JobStatus, exitCode: number | null): boolean {
  return status === "timed_out" || (status === "exited" && exitCode !== 0)
}

const STRIP_JOBS = 3
const TAIL_LINES = 4

// One row per running job above the composer, each with the newest lines it printed.
// The agent's own screen while a skill shows one: a small live preview from Kvist's loopback
// viewer (view only); a click opens the full viewer in an IDE editor tab.
export function ScreenStrip({ screen, stream, onOpen, russian = false }: { russian?: boolean; screen: { title: string; state: string; url: string } | null; stream: string; onOpen: (url: string) => void }) {
  if (!screen) return null
  return (
    <div class="screen-strip">
      <div class="job-head">
        <span class="job-dot" aria-hidden="true" />
        <span class="job-label">{screen.title}</span>
        <span class="job-time">{screen.state}</span>
        <button class="link" title={russian ? "Открыть браузер агента во вкладке IDE" : "Open the agent’s browser in an IDE tab"} onClick={() => onOpen(screen.url)}>{russian ? "Открыть в IDE" : "Open in IDE"}</button>
      </div>
      {stream ? <img class="screen-thumb" src={stream} alt="The agent's own screen (view only)" onClick={() => onOpen(screen.url)} /> : null}
    </div>
  )
}

export function JobStrip({ jobs, russian, now, compact = false }: { jobs: readonly JobView[]; russian: boolean; now: number; compact?: boolean }) {
  const running = jobs.filter((job) => job.status === "running")
  if (running.length === 0) return null
  const copy = jobsCopy(russian)
  const shown = running.slice(0, STRIP_JOBS)
  return (
    <div class="jobs" aria-label={copy.running}>
      {shown.map((job) => {
        const lines = outputLines(job.output).slice(compact ? -1 : -TAIL_LINES)
        return (
          <div key={job.id} class="job">
            <div class="job-head">
              <span class="job-dot" aria-hidden="true" />
              <span class="job-label" title={job.command.join(" ")}>{jobLabel(job)}</span>
              <span class="job-time">{formatSeconds(Math.max(0, (now - job.startedAt) / 1000))}</span>
            </div>
            <pre class="job-out">{lines.length > 0 ? lines.join("\n") : copy.noOutput}</pre>
          </div>
        )
      })}
      {running.length > STRIP_JOBS ? <div class="job-more">{copy.more(running.length - STRIP_JOBS)}</div> : null}
    </div>
  )
}
