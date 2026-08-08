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
`update_live(user_message, status, step, progress=0, plan_step="working", is_error=False)`,
`print_plan(plan_info)`, `print_step_header(step, command)`, `debug_print(message)`.

**Ответы:**
`print_final(final_message, user_messages=None)`, `print_user_markdown(message)`,
`print_result(message)`, `print_error(message)`, `print_sandbox_notice(note)`,
`render_user_markdown(message, fallback_style="") -> str`,
и стриминг через `live_markdown()` — контекст-менеджер, отдающий callback
`update(message)` (каждый вызов заменяет весь отрендеренный ответ новым кумулятивным
текстом).

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
| `hello` | `protocol_version:int` | handshake; всегда первым |
| `live_start` / `live_stop` | — | живой статус начался / закончился |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | прогресс по шагу |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, progress:int` | выбранное действие шага |
| `plan` | `goal:str, complexity:str, plan:string[], success_criteria:string[], risks:string[]` | план задачи |
| `sandbox_notice` | `note:str` | одноразовое уведомление о песочнице |
| `stream_start` | `stream_id:str` | начало стримингового ответа |
| `stream_chunk` | `stream_id:str, text:str` | текущий кумулятивный текст (**снапшот**, не дельта) |
| `stream_commit` | `stream_id:str, text:str` | финальный стрим-текст (однократно) |
| `message` | `markdown:str` | не-стримовое Markdown-сообщение |
| `final` | `message:str, user_messages:string[]` | финальный ответ прогона (терминальное событие) |
| `debug` | `message:str` | verbose-диагностика |
| `error` | `message:str` | строка ошибки (эмитится и если прогон бросил исключение) |

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
>>> {"type": "hello", "protocol_version": 1}
>>> {"type": "status", "user_message": "Choosing provider", "status": "working", "step": 1, "progress": 10, "plan_step": "inspect", "is_error": false}
>>> {"type": "input_request", "request_id": "b7dca6b0…", "kind": "choice", "title": "Pick provider", "body": "", "options": [{"value": "openai", "label": "OpenAI"}, {"value": "freeflow", "label": "FreeFlow"}], "current_value": "", "help_text": ""}
<<< {"request_id": "b7dca6b0…", "value": "freeflow"}
>>> {"type": "final", "message": "picked freeflow …", "user_messages": []}
```

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

API `EventSession`: `submit(user_goal, routed_role=None)`, `events()` (итератор;
заканчивается, когда worker завершился и очередь пуста), `respond(request_id, *,
value=None, approved=None)`, `close(timeout=5.0)`, `is_running()`.

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
    case "hello":        checkVersion(ev.protocol_version); break;
    case "stream_chunk": appendStreaming(ev.text);          break;  // эффект живого набора
    case "status":       showStatus(ev);                    break;
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
- **Терминал — особый дефолт.** Его REPL добавляет slash-команды, сессии и визарды
  конфигурации сверх событийной поверхности; они пока не выведены в события.
- **Один процесс = один прогон/сессия** в v1. Для нескольких одновременных клиентов —
  процесс на сессию или мультиплексирующий socket-транспорт.

---

## 12. Файлы

Шов ядра: `ui.py`, `approvals.py`, `terminal_banner.py`.
Протокол событий: `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py`,
`event_stdio.py`, `frontends.py`.
Тесты: `tests/test_frontend_protocol.py`, `tests/test_event_stream_ui.py`,
`tests/test_event_session.py`, `tests/test_frontends.py`.
