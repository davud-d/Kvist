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
| `hello` | `protocol_version:int`; опционально `provider:str, models:object, role:str, approval_mode:str, session:str, has_checkpoint:bool` | handshake и эффективная конфигурация прогона; всегда первым |
| `live_start` / `live_stop` | — | живой статус начался / закончился |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | прогресс по шагу |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, reasoning_seconds:number` | выбранное действие шага; `reasoning_seconds` — сколько модель думала перед ним (0, если не думала) |
| `plan` | `goal:str, complexity:str, plan:string[], success_criteria:string[], risks:string[]` | план задачи |
| `sandbox_notice` | `note:str` | одноразовое уведомление о песочнице |
| `stream_start` | `stream_id:str` | начало стримингового ответа |
| `stream_chunk` | `stream_id:str, text:str` | текущий кумулятивный текст ответа (**снапшот**, не дельта) |
| `stream_commit` | `stream_id:str, text:str` | финальный текст ответа (однократно) |
| `reasoning_start` | `stream_id:str` | модель начала думать; тот же `stream_id`, что у ответа |
| `reasoning_chunk` | `stream_id:str, text:str` | текущий кумулятивный текст **раздумий** (снапшот, отдельный канал от ответа) |
| `reasoning_commit` | `stream_id:str, duration_seconds:number` | раздумья закончились (сейчас пойдёт ответ); несёт длительность |
| `message` | `markdown:str` | не-стримовое Markdown-сообщение |
| `final` | `message:str, user_messages:string[]` | финальный ответ прогона (терминальное событие) |
| `usage` | `input_tokens:int, output_tokens:int, context_tokens:int, context_limit:int, model:str, kind:str, step:int` | расход токенов одного завершённого вызова модели. `input_tokens`/`output_tokens` — это **траты**: их суммируют по прогону. `context_tokens` — **размер именно этого промпта**, то есть самого диалога, поэтому его надо *заменять*, а не суммировать (0 у вызовов, не несущих диалог, например `kind` `plan` или `report` — тогда сохраняйте прошлое значение). Контекст пересылается на каждом шаге, поэтому сумма трат уходит далеко вперёд от `context_tokens`: показ суммы под словом «токены» читается как гигантский диалог, которого нет. `context_limit` — бюджет, до которого ядро подрезает промпт. |
| `debug` | `message:str` | verbose-диагностика |
| `error` | `message:str` | строка ошибки (эмитится и если прогон бросил исключение) |

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
- В структурном цикле coder/devops раздумья по шагу отдаются компактно через
  `step.reasoning_seconds`, а не живым блоком.

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

```json
{"ok": false, "error": "Unknown session: 'ghost'"}
```

Семантика (идентична терминальным командам `/session` и `/new`):

- `status` — активная сессия, список сессий, сводка checkpoint (`null`, если нет).
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
  `--resume` (§8a) — с той же семантикой, что и терминальные команды.
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
