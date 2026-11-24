// Ported from frontends/tui/ink/ui/skins/claude-code/verbs.ts: the same words, so the two interfaces say the same things.
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

const JOKES = [
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

const MAXIMS = [
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

const JOKES_RU = [
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
  "Помечаю, что не запускала",
  "Держу отчёт честным",
]

const MAXIMS_RU = [
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
  "Каждое утверждение — от того, что проверила сама",
  "Пробел, который не закрыть, называю пробелом",
  "Найти — ещё не значит узнать",
  "Хожу кругами — значит, нет вопроса",
  "Шаг нужен, когда назван факт, который он выяснит",
  "Запрос — спецификация, теория — лишь гипотеза",
  "Что тронула — моё целиком",
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

export const VERBS = [...JOKES, ...MAXIMS]
export const VERBS_RU = [...JOKES_RU, ...MAXIMS_RU]
