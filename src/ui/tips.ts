// The "Tip:" line under the spinner while a turn runs, as in Claude Code: a hint
// about something Kvist can do, changing every few seconds. Each tip names a command the
// person can type, with the names the core really has (`/style` offers "Chat" and
// "Agents", each with the styles listed by main.get_style_options). When a command or
// style is renamed, re-read this list. Russian is picked by the language of the request.

export const TIPS: readonly string[] = [
  "Want a friendlier agent? /style → Agents → Friendly Developer",
  "Want a senior's judgement without spelling it out in the prompt? /style → Agents → Senior-dev",
  "Afraid of steps that can't be undone? /style → Agents → Paranoid looks before it acts",
  "Building a prototype? /style → Agents → Fast-and-dirty says so up front",
  "Want the cause, not just the fix? /style → Agents → Explainer",
  "Want tidy structure over speed? /style → Agents → Architect or Minimalist",
  "Chat too talkative? /style → Chat → No-Friendly keeps to the work",
  "Chat too agreeable? /style → Chat → Honest doesn't just go along",
  "Make it think harder with /effort: high, xhigh or max; false turns reasoning off",
  "Skip the separate planning call with /plan to start working at once",
  "Only want a review? /role reader can't write files; auto lets Kvist choose",
  "Changed your mind? /changes lists what the agent changed, /revert undoes it when safe",
  "Stopped a run halfway? /resume continues from its last checkpoint",
  "Start clean without losing anything: /new keeps the old session under /session",
  "Fast, smart and planner models are picked separately with /models",
  "/copy puts the last answer on your clipboard, /copy 2 the one before",
  "Hold Shift while dragging to select with your terminal instead of Kvist",
  "PageUp and PageDown scroll the conversation, Ctrl+End jumps back to the bottom",
  "Project rules go in KVIST.md at the workspace root; the agent rereads it every step",
  "Allow or deny commands per project in .kvist/permissions.json",
  "Choose \"don't ask again\" in an approval to add that command to the project's allow list",
  "Esc twice stops a run, Ctrl+C twice quits",
  "Ctrl+O shows the shell commands with their output, Ctrl+O again folds them",
  "/tui switches between the Ink, Claude-style and classic terminal",
  "/rename gives the session a title of your own",
  "Provider slow or rate-limited? /settings-api tunes pacing, timeouts and retries",
]

export const TIPS_RU: readonly string[] = [
  "Хочешь дружелюбного агента? /style → Agents → Friendly Developer",
  "Нужен синьор, но не хочешь объяснять это в промпте? /style → Agents → Senior-dev",
  "Боишься необратимых шагов? /style → Agents → Paranoid смотрит, прежде чем действовать",
  "Пишешь прототип? /style → Agents → Fast-and-dirty честно скажет об этом сразу",
  "Нужна причина, а не только правка? /style → Agents → Explainer",
  "Важнее порядок, чем скорость? /style → Agents → Architect или Minimalist",
  "Чат слишком болтлив? /style → Chat → No-Friendly держится дела",
  "Чат поддакивает? /style → Chat → Honest не идёт на поводу",
  "Пусть думает глубже: /effort high, xhigh или max; false отключает рассуждения",
  "Не нужен отдельный этап планирования? /plan выключит его",
  "Нужен только разбор? /role reader не пишет файлы; auto — пусть Kvist выберет сам",
  "Передумал? /changes покажет правки агента, /revert откатит их, если это безопасно",
  "Остановил запуск на полпути? /resume продолжит с последней точки",
  "Начать с чистого листа, ничего не потеряв: /new, старая сессия останется в /session",
  "Модели fast, smart и planner выбираются отдельно: /models",
  "/copy копирует последний ответ, /copy 2 — предыдущий",
  "Зажми Shift и тяни мышью: выделит сам терминал, а не Kvist",
  "PageUp и PageDown листают разговор, Ctrl+End возвращает вниз",
  "Правила проекта — в KVIST.md в корне: агент перечитывает его на каждом шаге",
  "Разрешить или запретить команды для проекта можно в .kvist/permissions.json",
  "В подтверждении выбери «don't ask again»: команда попадёт в разрешённые для проекта",
  "Esc дважды останавливает запуск, Ctrl+C дважды закрывает Kvist",
  "Ctrl+O показывает shell-команды с их выводом, повторное нажатие сворачивает",
  "/tui переключает Ink, интерфейс в стиле Claude и классический терминал",
  "/rename задаёт сессии своё название",
  "Провайдер тормозит? /settings-api: темп запросов, таймауты и повторы",
]

const CYRILLIC = /\p{Script=Cyrillic}/u

// A new tip every few seconds while a turn runs. The stride is coprime with the length of both
// lists, so the tips neither repeat before all have been shown nor come in the order of the list.
const TIP_SECONDS = 3
const TIP_STRIDE = 7

// `seed` is the turn's start time (where the turn begins in the list), `request` the text it
// answers, `elapsedSeconds` how long it has run.
export function tipFor(seed: number, request = "", elapsedSeconds = 0): string {
  const list = CYRILLIC.test(request) ? TIPS_RU : TIPS
  const step = Math.floor(Math.max(0, elapsedSeconds) / TIP_SECONDS)
  return list[(Math.floor(seed / 1000) + step * TIP_STRIDE) % list.length] ?? ""
}
