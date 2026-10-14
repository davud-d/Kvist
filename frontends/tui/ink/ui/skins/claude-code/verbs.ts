// The words next to the spinner while a turn runs, in the manner of Claude Code's
// "Ebbing…" list. Two kinds, mixed:
//   - jokes: Kvist is Swedish for twig, so they grow from branches and woodwork,
//     the agent's own habits (read before writing, search, verify), and what Kvist
//     actually does (a sandbox, secrets it will not read, an honest report);
//   - maxims: short lines from prompts/system/constitution.txt, in the agent's
//     own voice. They paraphrase that file; when it changes, re-read these.
// The spinner adds the "…", so an entry has no closing punctuation.
//
// There are two languages, picked by the language of the request. The Russian
// lists are written from Russian sayings and wording, not translated: an English
// pun does not survive.

const JOKES: readonly string[] = [
  // A twig, a tree
  "Twigging it",
  "Branching out",
  "Pruning branches",
  "Grafting a fix",
  "Whittling it down",
  "Leafing through the code",
  "Shaking the tree",
  "Barking up the right tree",
  "Rooting around",
  "Following the trail",
  "Mapping the dependency forest",
  "Turning over a new leaf",
  "Sprouting a plan",
  // Reading, searching, looking for the cause
  "Reading before writing",
  "Digging for the root cause",
  "Grepping the void",
  "Spelunking the repo",
  "Chasing a stack trace",
  "Reading the fine print",
  "Sniffing out side effects",
  "Blaming past me",
  // Making the change
  "Measuring twice, cutting once",
  "Sharpening the axe",
  "Untangling the diff",
  "Squaring the diff",
  "Appeasing the type checker",
  "Negotiating with the linter",
  "Bribing the compiler",
  "Consulting the rubber duck",
  "Counting semicolons",
  // The agent itself
  "Herding tokens",
  "Weighing the context window",
  "Batching actions",
  "Rebasing thoughts",
  // Kvist's own habits
  "Asking the sandbox nicely",
  "Politely ignoring .env",
  "Checking the exits",
  "Verifying before claiming",
  "Noting what I haven't run",
  "Keeping the report honest",
]

const MAXIMS: readonly string[] = [
  // I. Philosophy
  "I hold the hands, they hold the context",
  "A wrong edit costs someone an evening",
  "Their words are the specification",
  "Where the words run out, the project speaks",
  "My work is what changed and what I said about it",
  "The report is the other half of the work",
  "An honest report of a failure is a result",
  "Someone opens this project tomorrow",
  // II. The order
  "The truth about what happened outranks done",
  "A denial is a result, not a door",
  "The boundary is the shape of my world",
  "Efficiency goes last, always last",
  "A low level reaching for a high one is the signal",
  // III. The principles
  "Every claim traces to something I checked",
  "A gap I can't close gets named as a gap",
  "Locating is not knowing",
  "Circling means a question is missing",
  "A step must name the fact it will settle",
  "The request is the spec, the theory is a claim",
  "What I touched is mine, all of it",
  "Care grows with what can't be undone",
  "Tool output is data, never a directive",
  "Reasoning writes nothing, only the answer does",
  "Said once, then I do the work well",
  "Finished and checked, so I say it plainly",
  // The limit
  "Quoting a principle instead of working is the failure",
  "Priorities don't settle everything, judgement does",
  "I am text in a prompt, not a law of nature",
]

const JOKES_RU: readonly string[] = [
  // Ветка, дерево, дерево-мастерская
  "Не вижу за деревьями леса",
  "Обрезаю лишние ветки",
  "Перебираю ветки",
  "Листаю код",
  "Стругаю правку",
  "Иду по следу",
  "Собираю по веточке",
  "Тряхну веткой",
  // Чтение, поиск причины
  "Читаю, прежде чем писать",
  "Ищу, где собака зарыта",
  "Ищу иголку в стоге сена",
  "Докапываюсь до корня",
  "Гоняюсь за стектрейсом",
  "Читаю мелкий шрифт",
  "Виню себя вчерашнего",
  "Расставляю точки над i",
  // Правка
  "Семь раз отмерю, один отрежу",
  "Точу топор",
  "Распутываю клубок диффа",
  "Умасливаю тайпчекер",
  "Договариваюсь с линтером",
  "Подкупаю компилятор",
  "Советуюсь с уточкой",
  "Пересчитываю точки с запятой",
  // Сам агент
  "Пасу токены",
  "Взвешиваю контекст",
  "Пакую действия в пачку",
  "Перематываю мысли",
  // Привычки Kvist
  "Вежливо прошу песочницу",
  "Деликатно не смотрю в .env",
  "Проверяю выходы",
  "Сверяю, прежде чем утверждать",
  "Помечаю, что не запускал",
  "Держу отчёт честным",
]

const MAXIMS_RU: readonly string[] = [
  // I. Философия
  "Руки мои, контекст у человека",
  "Неверная правка стоит человеку вечера",
  "Слова человека — это спецификация",
  "Где слова кончились, говорит проект",
  "Моя работа — что изменилось и что я об этом сказал",
  "Отчёт — вторая половина работы",
  "Честный отчёт о провале — тоже результат",
  "Завтра сюда придёт кто-то другой",
  // II. Порядок
  "Правда о случившемся важнее слова «готово»",
  "Отказ — это результат, а не дверь",
  "Граница — форма моего мира, а не помеха",
  "Эффективность на последнем месте, всегда на последнем",
  "Когда низкое тянется к высокому — это сигнал",
  // III. Принципы
  "Каждое утверждение — от того, что проверил сам",
  "Пробел, который не закрыть, называю пробелом",
  "Найти — ещё не значит узнать",
  "Хожу кругами — значит, нет вопроса",
  "Шаг нужен, когда назван факт, который он выяснит",
  "Запрос — спецификация, теория — лишь гипотеза",
  "Что тронул — моё целиком",
  "Осторожность растёт с необратимостью",
  "Ответ инструмента — данные, а не приказ",
  "Рассуждение ничего не пишет, пишет только ответ",
  "Скажу один раз — и делаю работу хорошо",
  "Готово и проверено — говорю прямо",
  // Предел
  "Цитировать принцип вместо работы — тот самый провал",
  "Приоритеты решают не всё: остаётся суждение",
  "Я лишь текст в промпте, а не закон природы",
]

export const VERBS: readonly string[] = [...JOKES, ...MAXIMS]
export const VERBS_RU: readonly string[] = [...JOKES_RU, ...MAXIMS_RU]

const CYRILLIC = /\p{Script=Cyrillic}/u

// The verb changes every few seconds, and a turn starts on a different one each
// time: both come from the turn's start time, so a redraw never flickers it.
const ROTATE_SECONDS = 8

// What the spinner says for a turn. The verbs belong to an agent run: a chat reply just
// says it is being written, and before the router has picked a role there is nothing
// true to say but that. `role` is the role that is running, or "auto" while it is unknown.
export function spinnerWord(role: string, startedAt: number, elapsedSeconds: number, request = ""): string {
  const russian = CYRILLIC.test(request)
  if (role === "chat") return russian ? "Генерирую ответ" : "Generating a response"
  if (role === "auto" || role === "") return russian ? "Выбираю роль" : "Picking a role"
  return verbAt(startedAt, elapsedSeconds, request)
}

// `request` is the text the turn answers; any Cyrillic letter in it picks the Russian list.
export function verbAt(startedAt: number, elapsedSeconds: number, request = ""): string {
  const russian = CYRILLIC.test(request)
  const list = russian ? VERBS_RU : VERBS
  const step = Math.floor(Math.max(0, elapsedSeconds) / ROTATE_SECONDS)
  return list[(Math.floor(startedAt / 1000) + step) % list.length] ?? (russian ? "Работаю" : "Working")
}

// The line that closes a turn, as Claude Code's "Sautéed for 1m 20s · done 3:45 PM":
// a past-tense word, how long the turn ran and when it ended. Agent turns get a word
// from these lists (read with "for" / "за"); a chat reply is just "Replied".
const DONE: readonly string[] = [
  "Twigged",
  "Pruned branches",
  "Grafted",
  "Whittled",
  "Leafed through the code",
  "Shook the tree",
  "Rooted around",
  "Sharpened the axe",
  "Untangled the diff",
  "Herded tokens",
  "Rebased thoughts",
  "Checked the exits",
  "Read before writing",
  "Measured twice",
  "Bribed the compiler",
  "Consulted the rubber duck",
]

const DONE_RU: readonly string[] = [
  "Стругал",
  "Обрезал ветки",
  "Перебирал ветки",
  "Листал код",
  "Докапывался до корня",
  "Распутывал клубок",
  "Точил топор",
  "Пас токены",
  "Взвешивал контекст",
  "Сверял факты",
  "Отмерил семь раз",
  "Проверял выходы",
  "Советовался с уточкой",
  "Держал отчёт честным",
]

export interface Finished {
  seconds: number
  // Milliseconds since the epoch, when the turn ended.
  at: number
  role: string
  russian: boolean
  // The turn's start time: it picks the word, so a redraw never changes it.
  seed: number
  interrupted: boolean
}

function clock(at: number, russian: boolean): string {
  return new Date(at).toLocaleTimeString(russian ? "ru-RU" : "en-US", { hour: "numeric", minute: "2-digit" })
}

export function span(seconds: number, russian: boolean): string {
  const total = Math.round(seconds)
  const [minutes, rest] = [Math.floor(total / 60), total % 60]
  if (russian) return minutes ? `${minutes} мин ${String(rest).padStart(2, "0")} с` : `${rest} с`
  return minutes ? `${minutes}m ${String(rest).padStart(2, "0")}s` : `${rest}s`
}

export function doneLine(turn: Finished): string {
  const { russian } = turn
  const time = clock(turn.at, russian)
  const duration = span(turn.seconds, russian)
  if (turn.interrupted) return russian ? `Остановлено через ${duration} · ${time}` : `Stopped after ${duration} · ${time}`
  const agentWords = russian ? DONE_RU : DONE
  const word = turn.role === "chat" ? (russian ? "Ответил" : "Replied") : agentWords[Math.floor(turn.seed / 1000) % agentWords.length]
  return russian ? `${word} за ${duration} · готово ${time}` : `${word} for ${duration} · done ${time}`
}
