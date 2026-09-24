# Kvist UI API — как делать фронтенды

*(English version: [UI_API.md](UI_API.md))*

Это **полный контракт** для того, чтобы поставить любой фронтенд поверх ядра агента
Kvist — терминал, web-приложение, Electron/JS GUI, Telegram-бота или плагин IDE.
Документ самодостаточен: человек или AI может сделать фронтенд по одному этому файлу,
**не читая Python-ядро**.

---

## 1. Идея

Ядро агента не зависит от фронтенда и никогда не пишет в терминал напрямую. Оно только:

- **отдаёт** то, что производит — живой статус, рассуждения плана/шага, стриминг
  текста, финальный ответ, ошибки; и
- **спрашивает** то, что ему нужно — выбор из меню, строку текста, секрет или
  подтверждение опасной команды (да/нет) — и **блокируется**, пока не ответят.

Терминал — это лишь фронтенд по умолчанию. Всё остальное — тонкий адаптер поверх того
же контракта. Способов подключения два.

---

## 2. Два режима интеграции

### Режим A — реализовать протоколы внутри процесса (тот же Python-процесс)

Напишите класс с методами `AgentUI` (§5) и, если обрабатываете опасные команды,
`ApprovalGate` (§6), затем внедрите их:

```python
agent    = LocalAgent(client, executor, config, ui=my_ui)   # agent.py
executor = SafeExecutor(workspace, approvals=my_gate)        # executor.py
```

`main.build_agent(..., ui=my_ui)` тоже принимает внедрённый UI и переиспользует
резолв провайдера/моделей/кредов. Режим **синхронный**: агент вызывает ваши методы на
своём потоке, поэтому методы ввода обязаны блокироваться до ответа пользователя.
Подходит для Python-GUI, который уже гоняет агента на worker-потоке.

### Режим B — поток событий (рекомендуется для GUI / web / Telegram / IDE / других языков)

Превратите границу в **данные**: ядро эмитит JSON-сериализуемые события и читает
JSON-ответы. Именно это нужно фронтендам в отдельных процессах и не на Python. Части
(только stdlib, без внешних зависимостей):

- `frontend_protocol.py` — типизированные события (у каждого `to_dict()`) +
  `PROTOCOL_VERSION` + потокобезопасный `FrontendChannel`.
- `event_stream_ui.EventStreamUI(channel)` — это `AgentUI`, который эмитит события;
  методы ввода блокируются на `channel.request(...)`.
- `event_session.EventSession(channel, agent)` — гоняет `agent.run(...)` на
  worker-потоке и отдаёт события; `EventApprovalGate(channel)` превращает
  подтверждения тоже в события.
- `event_stdio.py` — готовый транспорт NDJSON.
- `frontends.py` — универсальный диспетчер `--frontend NAME` (§9).

**Фронтенд не на Python ничего из этого не импортирует.** Он просто запускает ядро как
дочерний процесс и говорит на JSON (§8).

---

## 3. Запуск фронтенда

```bash
kvist --workspace /path/to/project --frontend stdio      # поток событий по stdio
kvist --workspace /path/to/project                       # терминальный REPL (по умолчанию)
```

- `--frontend terminal` (по умолчанию) → интерактивный терминальный REPL.
- `--frontend stdio` → NDJSON в stdout/stdin (§8).
- `--frontend <name>` → внешний фронтенд `kvist_frontends/<name>.py` (§9).
- `--frontend stdio --resume` → возобновление checkpoint активной сессии вместо
  новой задачи (строка задачи из stdin не читается; §8a).
- `--session-op …` → одна headless-операция жизненного цикла сессий, JSON в
  stdout (§8a).
- `--commit-message` → сообщение коммита по набору изменений из stdin, JSON в
  stdout (§8b).

(В разработке, без собранного бинаря:
`.venv/bin/python main.py --workspace … --frontend stdio`.)

---

## 4. Версия протокола и правила совместимости (ПРОЧИТАЙ)

- **Первое событие в любом потоке — `hello`** с полем `protocol_version` (сейчас `1`).
  Проверяйте его; при несовпадении откажитесь или адаптируйтесь.
- **`protocol_version` растёт только при ломающем изменении** существующих форм
  событий/ответов.
- **Добавление нового `type` события — НЕ ломающее.** Фронтенд **ОБЯЗАН игнорировать
  неизвестные типы событий** (forward-compat). Свитч по `type`, неизвестное —
  пропускать.
- В существующее событие могут *добавляться* поля без смены версии; не полагайтесь на
  точный набор ключей — читайте только нужные.

---

## 5. `AgentUI` — поверхность вывода + ввода (`ui.py`)

Структурный `typing.Protocol` (наследование не нужно). Методы по группам:

**Живой статус / рассуждения (fire-and-forget):**
`start_live()`, `stop_live()`,
`update_live(user_message, status, step, progress=0, plan_step="working", is_error=False)`
(`progress` вычисляется рантаймом из выполненных шагов плана и `plan_step`; модель его не сообщает),
`print_plan(plan_info)`, `print_step_header(step, command)`, `debug_print(message)`.

**Ответы:**
`print_final(final_message, user_messages=None)`, `print_user_markdown(message)`,
`print_result(message)`, `print_error(message)`, `print_sandbox_notice(note)`,
`render_user_markdown(message, fallback_style="") -> str`,
и стриминг через `live_markdown()` — контекст-менеджер, отдающий callback
`update(message)` (каждый вызов заменяет весь отрендеренный ответ новым кумулятивным
текстом). У callback может быть атрибут `update.reasoning(message)` — второй канал,
стримящий кумулятивный текст раздумий модели; вызывающий берёт его через
`getattr(update, "reasoning", None)`, поэтому UI без канала раздумий продолжают работать.

**Ввод (блокирующий, возвращает ответ):**
`choose_option(title, options, current_value="", help_text="") -> str | None`,
`prompt_text(title, current_value="", help_text="") -> str | None`,
`prompt_secret(title, current_value="", help_text="") -> str | None`.

**Атрибут:** `verbose: bool`.

По умолчанию: `terminal_banner.TerminalBannerUI` (Rich; без TTY вырождается в простой
поток лог-строк).

---

## 6. `ApprovalGate` — подтверждения с участием человека (`approvals.py`)

Структурный `Protocol` с двумя методами:

- `confirm_command(command: list[str], reason: str) -> bool` — **опасная shell-команда**
  (argv + причина).
- `confirm(title: str, body: str, question: str) -> bool` — **перезапись файла** или
  запрос политики.

По умолчанию `TerminalApprovalGate` рисует запрос и читает y/N; он **fail-closed без
TTY** (бросает исключение, а не молча разрешает). Другой gate внедряется через
`SafeExecutor(workspace, approvals=...)`. В потоке событий `EventApprovalGate`
превращает каждый вызов в `input_request` с `kind == "confirm"` и возвращает
`bool(approved)` — поэтому отключённый/закрытый фронтенд **отказывает** (остаётся
fail-closed).

---

## 7. Каталог событий

Каждое событие — JSON-объект с полем `"type"`. Исходящие (ядро → фронтенд):

| `type` | Поля | Смысл |
|---|---|---|
| `hello` | `protocol_version:int`; опционально `provider:str, models:object, role:str, approval_mode:str, session:str, has_checkpoint:bool, effort:str, planning:bool, commands:string[]` | handshake и эффективная конфигурация прогона; всегда первым. `effort` (уровень reasoning), `planning` (этап планирования включён) и `commands` (имена, которые принимает `command`, включая `resume`) отправляет `--frontend stdio-multi`. |
| `live_start` / `live_stop` | — | живой статус начался / закончился |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | прогресс по шагу; во время вызова `user_message` — подпись вызова, которую строит ядро («читает src/x.py») |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, reasoning_seconds:number, action_description:str, call_id:str, batch_id:str` | один вызов инструмента из ответа модели. `reasoning_seconds` — сколько модель думала перед ним (0, если не думала). `action_description` — подпись вызова, ядро строит её из аргументов («читает src/x.py», `ищет "role" в src/`; у `run_shell` это purpose от модели). `batch_id` называет ответ модели: вызовы одного ответа делят его, их можно показать группой. `call_id` называет вызов, его `action_result` несёт тот же. `thought` всегда `""` (модель его больше не пишет). `user_message` — текст ответа для человека на первом `step` пачки и `""` на остальных, поэтому фронтенд, который показывает его на каждом шаге, покажет его один раз. |
| `action_result` | `step:int, action_type:str, ok:bool, summary:str, output:str, exit_code:int\|null, path:str\|null, first_line:int\|null, call_id:str, batch_id:str` | итог одного выполненного действия (сопоставляйте с шагом по `call_id` или по `step`). Читающие вызовы одного ответа выполняются параллельно, поэтому несколько `step` могут прийти раньше своих результатов; результаты всё равно приходят в порядке вызовов. Пишущий вызов, пропущенный из-за упавшей записи в том же ответе, приходит с `ok:false` и сводкой «Skipped: an earlier write failed». `summary` — одна короткая строка («Wrote 3 lines», «Command exited with code 0», «2 matches»). `output` — вывод команды после редакции секретов (`run_shell`, `git_status`, `git_diff`, `delegate_task`) или текст ошибки неудачного действия; ограничен 4000 символами: сохраняются начало и конец, между ними маркер `[... N characters omitted ...]`. `exit_code` задан только у `run_shell` (`null`, если команда отклонена или упала по таймауту). `path` и `first_line` заданы для `write_file`/`append_file`/`replace_in_file`: `first_line` — 1-based строка получившегося файла, с которой начинается записанный текст (`1` для `write_file`, первая строка дописанного текста для `append_file`, первое заменённое вхождение для `replace_in_file`), чтобы можно было нумеровать строки диффа; у неудачной записи `null`. Не отправляется для `final_answer`/`report_to_user`/`refuse` и для действий, отклонённых защитой рантайма до выполнения (повтор, read-only, корректирующее действие); отказ по лимиту web_search/URL приходит как `ok:false`. |
| `action_output` | `step:int, output:str, call_id:str` | живой вывод выполняющейся команды `run_shell`: приходит, пока она работает, и до её `action_result`. `output` — **снапшот** последнего вывода (не больше 4000 символов, редакция секретов применена ко всему целиком), а не дельта, поэтому заменяйте показанное. События идут не чаще раза в ~120 мс, и последнее не теряется. Это только предпросмотр: полный ограниченный вывод по-прежнему приходит в `action_result`, который завершает живой вывод этого `step`. Команда, которая ничего не печатает, не шлёт ничего. |
| `route` | `role:str, source:str` | роль, под которой фактически идёт ход; отправляется один раз за ход, до плана или ответа. `role` — `coder`, `reader`, `devops` или `chat`. `source` — `explicit` (роль передал вызывающий), `direct` / `llm` / `keyword_fallback` / `default` (как решил роутер) или `resume` (голое «продолжай» после остановленного прогона унаследовало роль того прогона). |
| `plan` | `goal:str, complexity:str, plan:string[], success_criteria:string[], risks:string[]` | план задачи |
| `sandbox_notice` | `note:str` | одноразовое уведомление о песочнице |
| `stream_start` | `stream_id:str` | начало стримингового ответа |
| `stream_chunk` | `stream_id:str, text:str` | текущий кумулятивный текст ответа (**снапшот**, не дельта) |
| `stream_commit` | `stream_id:str, text:str` | финальный текст ответа (однократно) |
| `reasoning_start` | `stream_id:str` | модель начала думать; тот же `stream_id`, что у ответа |
| `reasoning_chunk` | `stream_id:str, text:str` | текущий кумулятивный текст **раздумий** (снапшот, отдельный канал от ответа) |
| `reasoning_commit` | `stream_id:str, duration_seconds:number` | раздумья закончились (сейчас пойдёт ответ); несёт длительность |
| `message` | `markdown:str` | не-стримовое Markdown-сообщение |
| `final` | `message:str, user_messages:string[], outcome:str` | финальный ответ прогона (терминальное событие). `outcome` — `"done"`, если прогон закончился и прошёл проверки завершения, иначе `"incomplete"` (честное «не сделано», остановка, бюджет шагов, отказ). |
| `usage` | `input_tokens:int, output_tokens:int, context_tokens:int, context_limit:int, model:str, kind:str, step:int` | расход токенов одного завершённого вызова модели. `input_tokens`/`output_tokens` — это **траты**: их суммируют по прогону. `context_tokens` — **размер именно этого промпта**, то есть самого диалога, поэтому его надо *заменять*, а не суммировать (0 у вызовов, не несущих диалог, например `kind` `plan` или `report` — тогда сохраняйте прошлое значение). Контекст пересылается на каждом шаге, поэтому сумма трат уходит далеко вперёд от `context_tokens`: показ суммы под словом «токены» читается как гигантский диалог, которого нет. `context_limit` — бюджет, до которого ядро подрезает промпт. |
| `debug` | `message:str` | verbose-диагностика |
| `error` | `message:str` | строка ошибки (эмитится и если прогон бросил исключение) |
| `turn_end` | — | только `--frontend stdio-multi`: ход завершён (после `final`, `error` или chat-ответа, у которого нет `final`); процесс остаётся жить для следующей цели |
| `session` | `session:str, has_checkpoint:bool` | только `--frontend stdio-multi`: первое событие хода `resume` — сессия, в которой продолжается прерванный прогон; может отличаться от сессии в `hello` (§8c). |
| `session_title` | `session:str, title:str` | название сессии стало известно: сгенерировано в фоне по первому завершённому ходу (никогда не по первому сообщению пользователя — см. §8a) или задано вручную командой `rename` (§8c). Дополнительное, внеполосное событие — может прийти намного позже `turn_end` хода, который его запустил, а `session` может называть не ту сессию, что активна сейчас, если пользователь успел переключиться, пока шла генерация. |
| `command_result` | `name:str, ok:bool, message:str, effects:object` | только `--frontend stdio-multi`: итог входящего сообщения `command` (§8c). Это последнее событие команды; **`turn_end` после него не приходит**. |

### Думающие модели (reasoning)

Некоторые модели «думают» перед ответом, иногда минутами. Kvist выносит эти раздумья
на **отдельный канал**, чтобы они не смешивались с ответом:

- **Детекция динамическая, без статического флага.** Фронтенд понимает, что модель
  думает, в момент прихода `reasoning_start` — не по имени модели. Если ответ пошёл
  без событий `reasoning_*`, значит модель не думала; рендерите как обычно.
- **`stream_id` раздумий совпадает с `stream_id` ответа.** Привязывайте UI раздумий к
  тому ответу, который они предваряют.
- **Порядок** для думающего ответа: `stream_start` → `reasoning_start` →
  `reasoning_chunk`* → `reasoning_commit` → `stream_chunk`* → `stream_commit`.
  `reasoning_commit` всегда до первого `stream_chunk`, поэтому мысли и ответ не
  перемешиваются. Типичный GUI показывает живой серый блок, пока модель думает, и
  сворачивает его в ярлык «Thought for Ns» (из `duration_seconds`), когда пошёл ответ.
- **Захват пассивный и провайдер-независимый**, только при включённом стриминге. Kvist
  читает reasoning-токены, которые провайдер и так шлёт (`reasoning` /
  `reasoning_content`), и ничего дополнительно не запрашивает. Учтите: собственные
  reasoning-модели OpenAI (o-серия) **не** стримят текст раздумий через
  `chat.completions` — придёт `reasoning_commit` (время), но серый блок останется
  пустым, и это честно.
- В структурном цикле coder/devops раздумья каждого шага стримятся так же, но отдельным
  потоком: `reasoning_start` → `reasoning_chunk`* → `reasoning_commit` с одним
  `stream_id`, у которого **нет** `stream_start` и нет ответа. Commit приходит, когда
  модель начинает писать действие, а следующее за ним событие `step` несёт
  `reasoning_seconds: 0`, потому что время уже передано. Шаг, на котором модель не
  думала, отдаёт `reasoning_seconds` в событии `step`, как раньше.

Входящий запрос / ответ:

| Поле `input_request` | Заметки |
|---|---|
| `request_id:str` | сопоставьте с ним свой ответ |
| `kind:str` | одно из `choice`, `text`, `secret`, `confirm` |
| `title:str` | короткий запрос |
| `body:str` | длинный текст (например, diff или argv+причина для `confirm`) |
| `options:object[]\|null` | для `choice`: `[{"value","label"[,"description"]}, …]` |
| `current_value:str` | текущее/дефолтное значение (пусто для `secret`) |
| `help_text:str` | опциональная подсказка |

**Ваш ответ — один JSON-объект:**

- `choice` / `text` / `secret` → `{"request_id": "...", "value": "<ответ>"}`
  (или `"value": null` для отмены).
- `confirm` → `{"request_id": "...", "approved": true|false}`.

**Редакция и секреты:** исходящие текстовые поля редактируются от секретов до попадания
в поток. Значение `secret` **никогда не эмитится наружу** — оно приходит только
входящим как ваш `value`. Не логируйте secret-ответы.

---

## 8. Транспорт stdio-JSON (`--frontend stdio`)

Кросс-процессная, кросс-язычная граница. Правила:

- **Кадрирование:** один JSON-объект на строку (NDJSON), UTF-8.
- **Направление:** события → **stdout**; ваши ответы → **stdin** (по одному
  JSON-объекту на строку).
- **Задача (goal)** — это **первая строка, которую вы пишете в stdin** (родитель
  управляет прогоном), если не передана в командной строке.
- **stdout — чистый JSON.** Ядро перенаправляет всё остальное (логи, случайные
  print, предупреждения) в **stderr**, поэтому stdout можно парсить построчно
  безопасно.
- **Порядок:** stdout строго упорядочен. Первая строка всегда `hello`; прогон
  завершается событием `final` (или `error`), после чего поток закрывается.
- **Модель блокировки:** агент работает на worker-потоке и блокируется на каждом
  `input_request`, пока не увидит соответствующий `input_response`. Не ответите — он
  ждёт; закроется процесс/поток — незакрытые подтверждения **отказывают**
  (fail-closed).

### Пример-транскрипт (реальный захват; `>>>` = stdout, `<<<` = ваш stdin)

```
<<< do the thing
>>> {"type": "hello", "protocol_version": 1, "session": "default", "has_checkpoint": false}
>>> {"type": "status", "user_message": "Choosing provider", "status": "working", "step": 1, "progress": 10, "plan_step": "inspect", "is_error": false}
>>> {"type": "input_request", "request_id": "b7dca6b0…", "kind": "choice", "title": "Pick provider", "body": "", "options": [{"value": "openai", "label": "OpenAI"}, {"value": "freeflow", "label": "FreeFlow"}], "current_value": "", "help_text": ""}
<<< {"request_id": "b7dca6b0…", "value": "freeflow"}
>>> {"type": "final", "message": "picked freeflow …", "user_messages": []}
```

---

## 8a. Именованные сессии для фронтендов

Каждый прогон выполняется в **активной именованной сессии** workspace (история
диалога, история чата и возобновляемый checkpoint для `/resume` живут в каждой
сессии отдельно). Событие `hello` сообщает это двумя аддитивными полями v1:
`session` (имя активной сессии) и `has_checkpoint` (есть ли в ней
возобновляемый checkpoint).

**Никогда не читайте и не пишите файлы `.kvist/` напрямую.** Управление
жизненным циклом сессий — отдельный one-shot режим того же бинаря без LLM:

```bash
kvist --workspace WS --session-op status
kvist --workspace WS --session-op list
kvist --workspace WS --session-op history --session-name feature [--history-limit N] [--history-before SEQ]
kvist --workspace WS --session-op launch
kvist --workspace WS --session-op new
kvist --workspace WS --session-op create --session-name feature
kvist --workspace WS --session-op switch --session-name feature
kvist --workspace WS --session-op delete --session-name old-work
kvist --workspace WS --session-op reset
```

Каждый вызов печатает **один JSON-объект** в stdout и завершается с кодом `0`
при успехе или `1` при ошибке:

```json
{"ok": true, "active_session": "feature", "sessions": ["default", "feature"],
 "checkpoint": {"goal": "…", "step": 6, "progress": 40},
 "resumable_session": "feature", "warnings": []}
```

`checkpoint` описывает только **активную** сессию; `resumable_session` называет
сессию с самым свежим checkpoint (`null`, если такой нет) — после `launch` это,
как правило, не активная сессия.

**Названия сессий.** `title` в каждом элементе `status`/`list` выше вычисляется
заново при каждом вызове (первая цель / первое сообщение чата / цель
checkpoint, иначе имя сессии). Отдельно от этого ядро также генерирует и
*сохраняет* короткое название прямо в документе сессии: один раз, в фоне, по
первому завершённому ходу сессии (никогда не по одному первому сообщению
пользователя — модель видит цель плюс первые 20 строк ответа). Если модель не
ответила вовремя, используется первое сообщение пользователя, обрезанное до 40
символов по границе слова, — сессия никогда не остаётся без названия. `rename`
(§8c) задаёт название вручную и с этого момента блокирует фоновую генерацию
для этой сессии. Сохранённое название (или тот же fallback, вычисленный на
лету для сессии, которая появилась раньше этой возможности или ещё не
получила название) — это то, что показывает список интерактивной команды
`session`, и то, о чём сообщает событие `session_title` (§7) в момент, когда
название становится известно — это может произойти заметно позже завершения
хода, который его запустил.

```json
{"ok": false, "error": "Unknown session: 'ghost'"}
```

Семантика (идентична терминальным командам `/session` и `/new`):

- `status` — активная сессия, список сессий, сводка checkpoint (`null`, если нет).
  Каждая запись `session_items` несёт `name`, `title`, `updated_at`,
  `has_checkpoint`, `journal_bytes` (размер журнала событий сессии, `0`, если
  его нет) и предназначенные для модели `session_history` / `chat_history`.
  Эти два поля — вся история каждой сессии, поэтому `status` растёт вместе с
  workspace (~570 КБ на 36 сессий).
- `list` — дешёвый `status`: тот же ответ без `session_history` и
  `chat_history` в записях. Используйте его для выбора сессии; остальной
  `status` не изменился и по-прежнему нужен Electron GUI.
- `history` — журнал событий одной сессии (ниже).
- `launch` — **вызывайте один раз при старте вашего приложения.** Запуск всегда
  открывает собственную новую сессию вместо предыдущей. Прерванный запуск не
  теряется: `status` называет сессию, где он остался, в `resumable_session`, а
  `--frontend stdio --resume` сам переключается в неё.
- `new` — создать **и активировать** новую сессию с именем, которое подбирает
  Kvist (терминальная `/new`). Уходящая сессия сохраняется и доступна для
  переключения.
- `create` — то же самое, но с вашим именем; ошибка, если такая уже есть.
- `switch` — активировать **существующую** сессию; отсутствующая или
  повреждённая цель даёт ошибку без смены активной сессии. Переключение никогда
  не создаёт сессии и не трогает файлы workspace.
- `delete` — удалить неактивную сессию (файл + резервная копия). Удаление
  активной сессии отклоняется.
- `reset` — опустошить активную сессию на месте (история, чат, checkpoint);
  имя сохраняется, другие сессии не затрагиваются. В отличие от `new`, это
  **уничтожает** ту переписку, на которой вызвано.

### Восстановление ленты сессии: `--session-op history`

Документ сессии хранит только то, что нужно модели (цель, роль, результат в 200
символов). Отдельно ядро ведёт **журнал того, что видел пользователь**: те же
события, что несёт поток из §8, в том виде, в каком они были отправлены, —
фронтенд перерисовывает старую сессию, проигрывая их через уже имеющийся
reducer. Журнал покрывает каждый ход, в том числе выполненный из терминала или
другим фронтендом.

```bash
kvist --workspace WS --session-op history --session-name feature --history-limit 500
```

```json
{"ok": true, "session": "feature", "has_more": true, "total_events": 1240,
 "skipped_lines": 0, "journal_bytes": 913442,
 "events": [
   {"seq": 812, "turn": 9, "ts": 1758560000.1, "event": {"type": "user", "text": "fix the tests"}},
   {"seq": 813, "turn": 9, "ts": 1758560000.9, "event": {"type": "route", "role": "coder", "source": "llm"}},
   {"seq": 820, "turn": 9, "ts": 1758560014.2, "event": {"type": "action_result", "step": 3, "action_type": "run_shell", "ok": true, "summary": "Command exited with code 0", "output": "12 passed\n", "exit_code": 0, "path": null, "first_line": null}}
 ]}
```

- `events` идут по порядку, старые первыми; `event` — в точности объект события
  из §8. `seq` уникален и растёт в пределах сессии, `turn` нумерует запросы
  пользователя (с 1), `ts` — время записи по настенным часам.
- **Записывается** (уже с редакцией секретов): `route`, `plan`,
  `sandbox_notice`, `step` (с полными `action_args`: у шага `run_shell` есть
  argv, у `write_file` — содержимое, у замены — `old_text`/`new_text`),
  `action_result` (вывод, код выхода, путь, `first_line`), мысли как
  `reasoning_start` / `reasoning_chunk` / `reasoning_commit` (**один**
  `reasoning_chunk` с итоговым текстом), ответ как `stream_commit`, `message`
  или `final` (целиком), `error` и по одному `usage` на ход (`kind: "turn"`,
  суммарные токены).
- **Не записывается** (только «вживую»): `status`, `live_start`/`live_stop`,
  `stream_chunk`, `action_output`, `debug`, `usage` по шагам, `hello`,
  `input_request`, `command_result`.
- **Добавляет сам журнал**: `user` — `{"type": "user", "text": …}`, запрос,
  открывший ход (`"resumed": true` для `/resume`), которого в потоке нет;
  `turn_end` закрывает каждый ход (с `"stopped_by_user": true`, если ход
  остановил пользователь); `journal_notice` сообщает, что что-то опущено:
  `{"omitted_events": N}` внутри хода, `{"omitted_turns": N}` первым событием
  журнала, когда старые ходы отброшены.
- Пагинация: возвращаются самые новые `--history-limit` событий (по умолчанию
  500, максимум 5000); `has_more` говорит, что есть более старые, а
  `--history-before SEQ` возвращает страницу, заканчивающуюся прямо перед
  `seq` `SEQ`. Страница может начинаться посреди хода.
- Потолки (усечённая строка сохраняет оба конца и содержит
  `[... N characters omitted ...]`): 20 000 символов на поле (записанное
  содержимое, аргументы, мысли), 100 000 на ответ, 200 элементов в списке,
  2 000 событий и 2 МиБ на ход (у ответов, ошибок и `turn_end` есть небольшой
  запас сверх этого), 8 МиБ и 500 ходов на сессию — сверх этого отбрасываются
  **самые старые ходы целиком**.
- `skipped_lines` считает строки журнала, которые не удалось разобрать (сбой
  может оборвать последнюю); они стоят только самих себя. Отсутствующий журнал —
  пустая история, поэтому сессии, созданные до появления журнала, читаются как
  `events: []`. Неизвестная сессия — `{"ok": false}`.
- Журнал только для показа: в промпт модели он не попадает. Он живёт и умирает
  вместе с сессией — `delete` и `reset` его удаляют, `new`/`create`/`switch` не
  трогают. Если журнал не удаётся записать, ход всё равно выполняется, а
  событие `debug` сообщает об этом.

Чтобы **возобновить** checkpoint активной сессии, запустите событийный фронтенд
с `--resume` вместо строки задачи:

```bash
kvist --workspace WS --frontend stdio --resume
```

Поток — обычный поток из §8; если checkpoint нет **ни в одной сессии**, ядро
отправляет событие `error` («No saved checkpoint to resume.») и завершает
прогон. Иначе ядро само активирует сессию, где он лежит, — поэтому поле
`session` в событии `hello` может отличаться от той, с которой вы запустились.

---

## 8b. Сообщения коммитов для IDE-фронтендов

Один вызов без потока событий: запишите изменения в stdin как JSON в **UTF-8**,
закройте stdin и прочитайте одну JSON-строку из stdout.

```bash
kvist --commit-message < payload.json
```

```json
{
  "files": [{"path": "src/app.py", "patch": "--- a/src/app.py\n+++ b/src/app.py\n..."}],
  "recent_subjects": ["Fix retry backoff", "Add AnyModel stub"]
}
```

- `files` — по записи на каждый изменённый файл; `path` относителен корня VCS,
  `patch` — unified diff файла. Передавайте то, что пользователь выбрал для
  этого коммита.
- `recent_subjects` — необязательно, заголовки последних коммитов (учитываются
  не больше 15). Сообщение подстраивается под их стиль: Conventional Commits,
  если проект их использует, иначе обычное предложение в повелительном
  наклонении.

Ответ (с ASCII-экранированием, чтобы кодовая страница консоли не мешала):

```json
{"ok": true, "message": "Retry provider calls on 429\n\nThe gateway ..."}
{"ok": false, "error": "Nothing to describe: no changed files with a diff."}
```

Код выхода 0 при успехе и 1 при ошибке. На что фронтенд может опираться:

- Сообщение всегда на английском, заголовок не длиннее 72 символов.
- Используются сохранённый провайдер и его **fast**-модель. Отсутствующий ключ —
  ошибка, а не запрос ввода, поэтому вызов не может зависнуть в ожидании.
- В рабочую папку ничего не пишется: ни сессии в `.kvist/`, ни `KVIST.md`.
- Секретные файлы (`.env`, ключи, сертификаты, `.ssh/`, ...) и `.kvist/`
  отбрасываются до того, как модель что-либо увидит; фильтровать их самим не
  нужно.
- Большой diff обрезается под модель; из-за размера вызов не падает.
- Всё остальное, что печатает ядро, идёт в stderr: его можно логировать, но не
  разбирать.

---

## 8c. Команды в процессе ядра (`--frontend stdio-multi`)

Слэш-команды и мастера настройки терминала выполняются в том же долгоживущем
процессе. Фронтенд их не переписывает: он шлёт одно сообщение, рисует каждый
диалог команды универсальным видом `input_request` и читает итог из
`command_result`.

```json
<<< {"type": "command", "name": "role", "args": []}
>>> {"type": "input_request", "request_id": "b7dc…", "kind": "choice", "title": "Select Agent Role", "body": "", "options": [{"value": "auto", "label": "Auto (automatic role detection)", "description": "…"}, {"value": "coder", "label": "Coder", "description": "…"}], "current_value": "auto", "help_text": "…"}
<<< {"request_id": "b7dc…", "value": "coder"}
>>> {"type": "command_result", "name": "role", "ok": true, "message": "Role set to: coder / Роль: coder", "effects": {"role": "coder"}}
```

**Входящее сообщение:** `{"type": "command", "name": str, "args": string[]}`.
`name` — имя слэш-команды без слэша (регистр не важен); `args` необязателен и
заранее отвечает на первый вопрос команды, так что
`{"type":"command","name":"role","args":["coder"]}` вообще не открывает диалог.
Диалоги — обычная пара `input_request` / ответ из §7; ответ `secret` идёт только
внутрь и никогда не отражается наружу: ни в событии, ни в `message`, ни в
`effects`, ни в логах. Разрушительные шаги (`revert`, `del-provider`,
`full-reset`) спрашивают через `kind: "confirm"` с `body`; «да» — только
`approved: true`, всё остальное (`false`, нет ответа, EOF) ничего не делает.

**Правила**

- Команда выполняется **только между ходами**. Пока идёт ход (или другая
  команда), ядро сразу отвечает `ok:false` и команду не запускает.
- Каждая принятая команда заканчивается ровно одним `command_result`; в отличие
  от хода **`turn_end` нет**. Неизвестное имя, отсутствующее имя и неверные
  `args` тоже получают `command_result` с `ok:false`.
- `ok:false` — команда не удалась (`message` объясняет почему). Отменённый
  пользователем диалог — это `ok:true` и `effects` `{"cancelled": true}`, ничего
  не изменилось. EOF на stdin отменяет открытый диалог так же.
- `message` — текст для человека, всегда `English / Русский`, с редакцией
  секретов.
- `effects` говорит, что изменилось, чтобы фронтенд обновил своё состояние.
  Ключи есть только для того, чего команда коснулась.

**Команды и их `effects`**

| `name` | `args` | `effects` |
|---|---|---|
| `role` | `[auto\|coder\|reader\|devops\|chat]` | `{"role": str}` — это же роль, с которой отправляются все следующие цели; сохраняется как глобальная настройка |
| `effort` | `[false\|low\|medium\|high\|xhigh\|max]` | `{"effort": str, "advisory": str}` — `advisory` объясняет, как уровень ложится на активного провайдера (может быть пустым) |
| `plan` | `[true\|false]` (без аргумента — переключить) | `{"plan": bool}` — сохраняется |
| `verbose` | `[true\|false]` (без аргумента — переключить) | `{"verbose": bool}` — только для этого процесса |
| `style` | `[chat\|agents ИМЯ]` | `{"style": {"type": str, "name": str, "applied_to_session": bool}}` — `false` значит, что текущая сессия сохраняет свой стиль до следующей сессии |
| `session` | `[switch ИМЯ]`, `[new [ИМЯ]]`, `[delete ИМЯ]`; без аргументов — выбор из списка | `{"session": str, "sessions": string[], "has_checkpoint": bool, "changed": bool}` |
| `new` | `[ИМЯ]` | как у `session` |
| `rename` | `ИМЯ` (обязателен; несколько аргументов склеиваются пробелом) | `{"session": str, "title": str}` — задаёт название активной сессии вручную; с этого момента блокирует фоновую генерацию названия для неё (§8a) |
| `models` | `[FAST SMART PLANNER]`; без аргументов — выбор для активного провайдера | `{"provider": str, "models": {"fast","smart","planner"}, "warnings": string[]}` — агент пересобран с этими моделями, модели сохранены |
| `changes` | — | `{}`; отчёт лежит в `message` |
| `provider` | `[PROVIDER_ID]`; без аргумента — выбор из списка (с «+ Create custom provider…») | `{"provider": str, "models": {…}, "warnings": string[], "changed": bool}`; при `changed:false` (тот же провайдер) только `provider` и `changed`. Агент пересобран, выбор сохранён. Сборка может открыть ещё диалоги: `text` (базовый URL), `secret` (API-ключ), `choice`/`text` (модели). |
| `change-api` | — | `{"provider", "models", "warnings", "key_saved": true}`. По одному диалогу `secret` на каждый ключ активного провайдера (пустой ответ оставляет ключ; все пустые = `{"cancelled": true}`). Если пересборка затем упала: `ok:false`, `{"key_saved": true, "restart_required": true}` |
| `del-provider` | — | только кастомный провайдер. Сначала `confirm`, затем замена выбирается тем же списком провайдеров: `{"deleted": str, "provider", "models", "warnings"}`. Замену не выбрали: `ok:false`, `{"deleted": str, "restart_required": true}` (работающий агент использует удалённый провайдер до перезапуска) |
| `full-reset` | — | два диалога `confirm`, второй называется «Final confirmation»; затем `~/.kvist/config.json` стирается, роль возвращается к auto, провайдер выбирается заново: `{"reset": true, "provider", "models", "warnings"}`. Ничего не выбрали: `ok:false`, `{"reset": true, "restart_required": true}` |
| `settings-api` | — | цикл-меню: `choice` (строка на настройку, «Reset all», «Done»), затем `choice` on/off или `text` для числа (спрашивается заново, пока не подойдёт). `{"changed_keys": string[], "values": {ключ: значение}, "restart_required": bool}`; `restart_required` = true, если менялась `dynamic_models` (применяется со следующего запуска) |
| `strime` | `[true\|false]` (без аргумента — переключить) | только кастомный провайдер (иначе `ok:false`): `{"stream": bool}` |
| `revert` | — | `confirm`, в `body` которого список файлов к восстановлению. `{"reverted": [{"path", "action": "restored\|deleted\|skipped", "reason"}]}`; нечего откатывать: `{"reverted": []}` и без диалога |

**`resume`** — не мастер, а ход агента: `{"type":"command","name":"resume"}`
продолжает самый свежий прерванный прогон в той сессии, где лежит его чекпоинт.
Если чекпоинта нет ни в одной сессии, сразу приходит
`command_result {"name":"resume","ok":false,…}` и больше ничего. Иначе поток
ровно такой же, как у обычной цели, плюс одно событие в начале:

```
>>> {"type": "session", "session": "chat-2026…", "has_checkpoint": true}
>>> … события хода (route/plan/step/action_result/… final или error) …
>>> {"type": "turn_end"}
```

У возобновлённого хода **нет `command_result`**; его завершает `turn_end`, а
`session` говорит фронтенду, какая беседа теперь активна. Как и обычный ход, он
не принимает `command`, пока идёт.

## 9. Универсальный диспетчер фронтендов (`frontends.py`)

Все не-терминальные фронтенды имеют одинаковую склейку (`FrontendChannel` →
`EventStreamUI` → `SafeExecutor(approvals=EventApprovalGate)` → `build_agent(ui=…)` →
`EventSession`), поэтому `frontends.py` строит сессию один раз и отдаёт её
**лаунчеру** — любому callable `launch(session)`.

- Встроенный: `stdio`.
- **Добавить фронтенд = один файл, без правки ядра.** Создайте
  `kvist_frontends/<name>.py` с `def launch(session): …` и запустите
  `kvist --frontend <name>`. Модуль импортируется **лениво** (тяжёлый тулкит вроде
  Qt/aiohttp грузится только при выборе).

Лаунчер получает готовый `EventSession` и управляет им:

```python
def launch(session):
    session.submit(goal)                 # старт прогона (worker-поток)
    for event in session.events():       # блокируется/поллит до конца прогона
        data = event.to_dict()
        if data["type"] == "input_request":
            session.respond(data["request_id"], value=...)      # или approved=True/False
        else:
            render(data)
    session.close()
```

API `EventSession`: `submit(user_goal, routed_role=None)`, `submit_resume()`
(продолжить checkpoint активной сессии), `events()` (итератор; заканчивается,
когда worker завершился и очередь пуста), `respond(request_id, *, value=None,
approved=None)`, `close(timeout=5.0)`, `is_running()`.

---

## 10. Другой язык (Electron / JS / что угодно)

JSON-граница означает, что «красота» может быть на любом языке, а Python-ядро работает
как **дочерний процесс** — классический паттерн «движок за stdio-RPC» (как LSP/DAP/git).
Ваше приложение не импортирует Python — только говорит на JSON из §7–§8.

```javascript
const { spawn } = require("child_process");
const readline = require("readline");

const proc = spawn("kvist", ["--workspace", ws, "--frontend", "stdio"]);
proc.stdin.write("do the thing\n");                 // задача = первая строка stdin
readline.createInterface({ input: proc.stdout }).on("line", (line) => {
  const ev = JSON.parse(line);
  switch (ev.type) {
    case "hello":            checkVersion(ev.protocol_version);        break;
    case "stream_chunk":     appendStreaming(ev.stream_id, ev.text);   break;  // эффект живого набора
    case "reasoning_start":  showThinking(ev.stream_id);               break;  // модель думает
    case "reasoning_chunk":  updateThinking(ev.stream_id, ev.text);    break;  // серые живые мысли
    case "reasoning_commit": collapseThinking(ev.stream_id, ev.duration_seconds); break; // «Thought for Ns»
    case "status":           showStatus(ev);                           break;
    case "final":        showFinal(ev.message);             break;
    case "error":        showError(ev.message);             break;
    case "input_request":
      askUser(ev).then((answer) => {
        const reply = ev.kind === "confirm"
          ? { request_id: ev.request_id, approved: answer }
          : { request_id: ev.request_id, value: answer };
        proc.stdin.write(JSON.stringify(reply) + "\n");
      });
      break;
    // default: игнорировать неизвестные типы событий (forward-compat)
  }
});
```

**Дистрибуция:** соберите ядро в замороженный бинарь (`kvist.spec` / PyInstaller) и
положите внутрь приложения — тогда конечным пользователям Python не нужен вообще.
Electron просто спавнит бинарь `kvist`.

**Браузер (не Electron):** stdio не подходит; напишите socket/websocket-лаунчер
(`kvist_frontends/websocket.py`), который качает тот же `FrontendChannel` через сокет.
Ядро не меняется — только транспорт.

---

## 11. Заметки, ограничения и гарантии (v1)

- **Креды.** `build_agent` резолвит креды провайдера заранее (из
  `~/.kvist/config.json`); headless-фронтенду они нужны **заранее настроенными**. Ввод
  кредов через события пока не подключён — настройте провайдера один раз через терминал
  или поставьте конфиг. Если провайдер/модель отсутствуют и нет TTY, ядро завершается с
  ненулевым кодом и сообщением в stderr (fail-closed).
- **Подтверждения fail-closed.** Без внедрённого gate — терминальные запросы; через
  события закрытый/молчащий фронтенд отклоняет опасные команды.
- **Цикл агента не менялся.** Фронтенды не вызывают внутренности агента — только
  рендерят события и отвечают на запросы.
- **Терминал — особый дефолт.** Его REPL добавляет slash-команды и визарды
  конфигурации сверх событийной поверхности. Управление же жизненным циклом
  сессий полностью доступно headless-фронтендам через `--session-op` и
  `--resume` (§8a) — с той же семантикой, что и терминальные команды. Под
  `stdio-multi` основные слэш-команды выполняются и в процессе ядра (§8c).
- **Один процесс = один прогон** в v1. Прогон выполняется в активной
  именованной сессии workspace; между прогонами сессии переключаются через
  `--session-op switch`. Для нескольких одновременных клиентов — процесс на
  workspace или мультиплексирующий socket-транспорт. Само хранилище сессий
  безопасно при конкурирующих записях (per-path блокировки + атомарная замена),
  но одновременная работа двух агентов в одном workspace не поддерживается.

---

## 12. Файлы

Шов ядра: `ui.py`, `approvals.py`, `terminal_banner.py`.
Протокол событий: `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py`,
`event_stdio.py`, `frontends.py`.
Headless-сессии: `session_store.py` (за `--session-op`).
Тесты: `tests/test_frontend_protocol.py`, `tests/test_event_stream_ui.py`,
`tests/test_event_session.py`, `tests/test_frontends.py`,
`tests/test_session_store.py`.
