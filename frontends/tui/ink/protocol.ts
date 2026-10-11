// Event shapes from UI_API.md §7. Only the fields the UI reads are typed;
// unknown event types are ignored by the reducer (forward compatibility).

export const PROTOCOL_VERSION = 1

export interface InputOption {
  value: string
  label: string
  description?: string
}

// What an approval is about; older cores omit it (UI_API.md §7, input_request).
export interface ApprovalSubject {
  kind: string
  command?: string
  reason?: string
  path?: string
  diff?: string
}

// How a background job ended; a running job has no status event.
export type JobStatus = "exited" | "timed_out" | "stopped"

export type InputKind = "choice" | "text" | "secret" | "confirm"

export interface InputRequest {
  request_id: string
  kind: InputKind
  title: string
  body: string
  options: InputOption[] | null
  current_value: string
  help_text: string
  subject?: ApprovalSubject | null
  remember_pattern?: string | null
}

export type CoreEvent =
  | { type: "hello"; protocol_version: number; provider?: string; models?: Record<string, string>; role?: string; approval_mode?: string; session?: string; has_checkpoint?: boolean; effort?: string; kvist_version?: string }
  | { type: "live_start" }
  | { type: "live_stop" }
  | { type: "status"; user_message: string; status: string; step: number; progress: number; plan_step: string; is_error: boolean }
  // call_id / batch_id are additive (v2 response contract): the calls of one model response
  // share a batch_id, and a call's result carries its call_id. An older core sends neither.
  // `thought` is always "" from a v2 core; `user_message` carries the response's text on the
  // first step of its batch only.
  | { type: "step"; step: number; plan_step: string; thought: string; user_message: string; action_type: string; action_args: Record<string, unknown>; reasoning_seconds: number; action_description?: string; call_id?: string; batch_id?: string }
  | { type: "action_result"; step: number; action_type: string; ok: boolean; summary: string; output: string; exit_code: number | null; path: string | null; first_line: number | null; call_id?: string; batch_id?: string }
  | { type: "action_output"; step: number; output: string; call_id?: string }
  | { type: "plan"; markdown: string }
  | { type: "route"; role: string; source?: string }
  | { type: "sandbox_notice"; note: string }
  | { type: "stream_start"; stream_id: string }
  | { type: "stream_chunk"; stream_id: string; text: string; append?: boolean }
  | { type: "stream_commit"; stream_id: string; text: string }
  | { type: "reasoning_start"; stream_id: string }
  | { type: "reasoning_chunk"; stream_id: string; text: string; append?: boolean }
  | { type: "reasoning_commit"; stream_id: string; duration_seconds: number }
  | { type: "message"; markdown: string }
  // `closed`: end_session's closing words or a closed session's reply, shown as an ordinary message.
  | { type: "final"; message: string; user_messages: string[]; outcome?: "done" | "incomplete"; closed?: boolean }
  | { type: "usage"; input_tokens: number; output_tokens: number; context_tokens: number; context_limit: number; model: string; kind: string; step: number }
  | { type: "debug"; message: string }
  | { type: "error"; message: string }
  | ({ type: "input_request" } & InputRequest)
  | { type: "command_result"; name: string; ok: boolean; message: string; effects?: Record<string, unknown> }
  | { type: "turn_end" }
  // Additive, out-of-band: a session's title became known in the background,
  // well after that turn's own turn_end, and possibly for a session the user
  // has since switched away from (UI_API.md §7).
  | { type: "session_title"; session: string; title: string }
  // The session a resume continues in or a session/new command switched to; `closed` and
  // `closed_notice` only for a session end_session closed (UI_API.md, "Closed sessions").
  | { type: "session"; session: string; has_checkpoint: boolean; closed?: boolean; closed_notice?: string }
  // Background shell jobs (run_shell with `background: true`). job_started, job_finished and
  // job_notice are recorded in the journal; job_output is a live-only snapshot of the job's tail
  // (replace, never append). Every job gets a job_finished before the turn's turn_end.
  | { type: "job_started"; job_id: string; command: string[]; purpose: string; call_id: string }
  | { type: "job_output"; job_id: string; output: string }
  | { type: "job_finished"; job_id: string; status: JobStatus; exit_code: number | null; output: string }
  // What the runtime told the model about jobs before its next request.
  | { type: "job_notice"; text: string; job_ids: string[] }
  // end_session closed the active session for good; `closed_notice` goes in place of the input.
  | { type: "session_ended"; session_id: string; warned_msg_id: number | null; closed_msg_id: number; reason: string; closed_notice: string }

// What `--session-op history` returns per event: the events the stream carried, plus two the journal adds
// (UI_API.md, "Restoring a session's feed").
export type JournalEvent =
  | CoreEvent
  | { type: "user"; text: string; resumed?: boolean }
  | { type: "journal_notice"; omitted_events?: number; omitted_turns?: number }

export type Reply =
  | { request_id: string; approved: boolean; remember?: true }
  | { request_id: string; value: string | null }
