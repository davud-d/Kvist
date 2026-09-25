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
  | { type: "hello"; protocol_version: number; provider?: string; models?: Record<string, string>; role?: string; approval_mode?: string; session?: string; has_checkpoint?: boolean; effort?: string; planning?: boolean; kvist_version?: string }
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
  | { type: "plan"; goal: string; complexity: string; plan: string[]; success_criteria: string[]; risks: string[] }
  | { type: "route"; role: string; source?: string }
  | { type: "sandbox_notice"; note: string }
  | { type: "stream_start"; stream_id: string }
  | { type: "stream_chunk"; stream_id: string; text: string }
  | { type: "stream_commit"; stream_id: string; text: string }
  | { type: "reasoning_start"; stream_id: string }
  | { type: "reasoning_chunk"; stream_id: string; text: string }
  | { type: "reasoning_commit"; stream_id: string; duration_seconds: number }
  | { type: "message"; markdown: string }
  | { type: "final"; message: string; user_messages: string[]; outcome?: "done" | "incomplete" }
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

// What `--session-op history` returns per event: the events the stream carried, plus two the journal adds
// (UI_API.md, "Restoring a session's feed").
export type JournalEvent =
  | CoreEvent
  | { type: "user"; text: string; resumed?: boolean }
  | { type: "journal_notice"; omitted_events?: number; omitted_turns?: number }

export type Reply =
  | { request_id: string; approved: boolean; remember?: true }
  | { request_id: string; value: string | null }
