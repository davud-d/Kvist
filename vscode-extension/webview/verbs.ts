// The action names are the core's vocabulary; a person reads what was done. Shared by
// the live feed (transcript.tsx) and the plain-text turn export (export.ts).
const VERBS: Record<string, string> = {
  read_file: "Read",
  read_multiple_files: "Read",
  list_dir: "Listed",
  list_tree: "Listed",
  find_files: "Found",
  search_code: "Searched",
  git_status: "Checked git status",
  web_search: "Searched the web",
  fetch_url: "Fetched",
  delegate_task: "Delegated a task",
}

export const verbOf = (action: string): string => VERBS[action] ?? action
