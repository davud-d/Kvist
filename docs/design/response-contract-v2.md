# Контракт ответа модели v2

Документ ведётся по фазам: сначала «как сейчас» (разведка), потом «как будет»
(дизайн). Строки указаны по `main` на коммите `52f6740`.

## Как сейчас

### Два режима ответа

Режим выбирается один раз на прогон в `agent_runtime.run_loop`
(`agent_runtime.py:586-587`): `RunState.native_tools` ставится из
`agent_model.native_tools_supported` (`agent_model.py:662`).

**JSON-режим** (без нативного tool calling).

- Каждый шаг промпт собирается заново: `agent_context.build_runtime_prompt`
  (`agent_runtime.py:594`), результат модели читает `agent_model.call_action_model`
  (`agent_model.py:613`).
- Ответ модели: ровно один JSON-объект
  `{"thought", "plan_step", "user_message", "actions": [...], "step_summary"}`.
  Описан в разделе `== 2. RESPONSE FORMAT ==` ролевых промптов:
  `prompts/system/coder_prompt.txt:20-41`, `prompts/system/devops_prompt.txt:25-46`,
  `prompts/system/reader_prompt.txt:17-30`. Примеры: `coder_prompt.txt:275-305`,
  `devops_prompt.txt:220-248`, `reader_prompt.txt:93-98`.
- Лимит «≤140 chars» для `thought` стоит только в промптах
  (`coder_prompt.txt:29`, `devops_prompt.txt:34`, `reader_prompt.txt:22`), парсер
  его не проверяет. Второй «140» живёт в `native_tools._parse`
  (`native_tools.py:264`): там `thought` режется до 140 символов.
- Разбор: `parser.parse_agent_commands` (`parser.py:82`). `thought`,
  `plan_step`, `user_message` обязательны (`parser.py:102-114`), `actions` от 1 до
  `MAX_BATCH_ACTIONS = 8` (`parser.py:39`, `136-148`), старое одиночное `action`
  тоже принимается. `final_answer`/`report_to_user` только последними, `refuse`
  только один (`parser.py:172-184`). Устойчивость: ```` ```json ```` обёртка,
  «умные» кавычки, висячие запятые, JSON посреди текста
  (`parser.py:430-485`).
- Каждый элемент пачки превращается в отдельный `AgentCommand` с общими
  `thought`/`user_message`/`plan_step` (`protocol.py:45-51`).

**Нативный режим** (`native_tools.py`).

- Растущий разговор в `RunState.native_messages` (`agent_runtime.py:746-765`).
- Системный промпт = ролевой промпт, где секции RESPONSE FORMAT / ARGS SCHEMAS /
  EXAMPLES / SELF-CHECK заменены на `prompts/system/native_tools.txt`
  (`native_tools.native_role_prompt`, `native_tools.py:210`).
- Текст рядом с вызовами идёт человеку. Ответ без вызовов = финальный ответ
  (`native_tools.final_answer_from_turn`, `native_tools.py:248`).
- Вызовы переводятся обратно в JSON-протокол и проходят тот же парсер
  (`native_tools._parse`, `native_tools.py:255-269`): `thought` = первая строка
  текста, обрезанная до 140, `user_message` = весь текст или подписи вызовов,
  `plan_step` выводится из имён инструментов (`native_tools._plan_step`).
  Ошибка в одном вызове роняет всю пачку (`agent_runtime.py:806-814`).
- Результат каждого вызова уходит `tool`-сообщением
  (`agent_runtime._run_native_batch`, `agent_runtime.py:818-843`).

**Исполнение пачки (оба режима).** `agent_runtime._run_action_batch`
(`agent_runtime.py:857`) и `_run_native_batch` идут строго по порядку и
останавливаются на первом заблокированном или упавшем действии
(`agent_runtime.py:874-878`). Параллельности нет. Хвост пачки попадает в
`pending_instruction` (`_note_skipped_actions`, `agent_runtime.py:882`).

**Искусственная задержка.** `_run_pipeline_dwell` (`agent_runtime.py:225-248`)
после каждого успешного шага JSON-режима спит `_pipeline_dwell_seconds`, пока
предзагружается следующий шаг. Для быстрых провайдеров выключено.

### Потребители `thought`, `user_message`, `report_to_user`

| Где | Строки | Что делает |
|---|---|---|
| `protocol.py` | 38-51 | поля `AgentCommand.thought`, `user_message`; `Action.description` (≤16 символов для баннера) |
| `parser.py` | 82-129, 172-184, 389-395 | требует `thought`/`user_message`, проверяет порядок финальных действий, схема `report_to_user` |
| `native_tools.py` | 56-60, 121, 255-269 | инструмент `report_to_user`, синтез `thought`/`user_message` из текста |
| `agent_runtime.py` | 214-222, 826, 866 | `_note_thought` → `ui.set_last_thought` (запасная колонка «мышления» в баннере) |
| `agent_runtime.py` | 918, 2312 | `record_user_message` → `RunState.user_messages` (сохраняется, уходит в `final`) |
| `agent_runtime.py` | 937-943, 1034, 1050, 1132, 1315 | `ui.update_live(user_message=command.user_message)` → событие `status` |
| `agent_runtime.py` | 1023-1025, 1167-1169, 1437-1439, 1658-1660, 2210, 2237, 2272 | копии `thought`/`user_message` в `step_logs` (лог прогона) |
| `agent_runtime.py` | 2396-2400 | `_build_plan_evidence_text`: `thought` и `user_message` участвуют в сопоставлении шагов плана со сделанным. Без них план закрывается хуже |
| `agent_runtime.py` | 1216-1256, 355-477 | `_finish_with_report` + `_render_report`: `report_to_user` рендерит отчёт отдельным вызовом модели по `report_prompt.txt` |
| `agent_runtime.py` | 1146-1213 | `_finish_with_final_answer`: гейты честности на `final_answer` |
| `agent_runtime.py` | 1396-1411 | `command_language_error` проверяет язык только у `final_answer.message` |
| `agent_web.py` | 27-34 | `thought` входит в защиту от повторного одинакового `web_search` |
| `agent.py` | 740-745 | синтетическая команда при `/resume` с `thought`/`user_message` = "[recovered after interruption]" |
| `agent_context.py` | 272-273, 327 | языковые правила в промпте называют `user_message`; `report_to_user` в списке финальных действий |
| `agent_model.py` | 1300-1320 | verbose-режим печатает поток, если в нём есть `"thought"` |
| `executor.py` | 418-423 | защитная заглушка для `report_to_user` |
| `frontend_protocol.py` | 81-99, 103-126, 157-170 | `StatusEvent.user_message`, `StepEvent.thought/user_message/action_description`, `FinalEvent.user_messages` |
| `event_stream_ui.py` | 99-117, 126-157, 282-290 | сериализация `status`, `step`, `final` |
| `session_events.py` | 250-260, 520-525 | журнал сессии пишет `step`/`final` как есть (живые `status` выкидывает) |
| `agent_logging.py` | 676, 759, 868 | `user_messages` в документе сессии и чекпоинте |
| `terminal_banner.py` (classic) | 223-229, 316-362, 414-418, 521-535 | `set_last_thought`, живая панель и строка статуса из `user_message`, verbose печатает `Thought:`/`Message:` |
| `banner.py` (classic) | 92-97, 535-537, 696-716 | «Last thought (no live reasoning)» в колонке мышления |
| `ui.py` | 30-40, 94 | интерфейс `AgentUI.update_live(user_message=…)`, `print_final(user_messages)` |
| `src/protocol.ts` | 39-40, 53 | типы `status`, `step`, `final` |
| `src/state.ts` (ts tui + webview VS Code) | 14-21, 83-87, 348, 355-377 | элементы `thought` и `say`; `say` берётся из `step.user_message`, для чтений пропускается; `thought` показывается, если не было потока reasoning; `status.text` = `user_message` |
| `src/ui/items.tsx`, `src/ui/live.tsx` | 67-80, 21-22 | отрисовка `thought` и `say` в ts tui |
| `vscode-extension/webview/transcript.tsx` | 20, 312-353, 553-557 | `HIDDEN_STEPS` = `final_answer`/`report_to_user`/`refuse`, отрисовка `thought`/`say` |
| `vscode-extension/webview/export.ts` | 109-115 | экспорт `thought`/`say` в текст |
| `gui/renderer/app.js` | 990-1000, 1172-1193 | статус из `user_message`, карточка шага: `action_description || user_message`, текст `thought` |
| jetbrains-plugin | — | событий не читает: запускает Kvist в терминальной вкладке |
| `UI_API.md` / `UI_API.ru.md` | 122-127, 173-187 | описание полей `status`, `step`, `final` |
| тесты Python | см. ниже | `conftest.make_command` (233-240) создаёт `AgentCommand(thought=…, user_message=…)` |

Упоминаний `thought|user_message` в тестах Python: `test_agent_runtime.py` 63,
`test_parser.py` 17, `test_agent_model.py` 14, `test_frontend_protocol.py` 10,
`test_role_context_and_report.py` 8, `test_regression_run_001.py` 6,
`test_cancellation.py` 6, `test_banner_live_panel.py` 6, ещё 16 файлов по 1-5.
TS: `tests/state.test.ts` и `vscode-extension/webview/export.test.ts`, всего 46 строк.

### `report_to_user` и конец хода

- `report_to_user` — терминальное действие. `run_loop` не отдаёт его
  исполнителю: `_run_one_action` (`agent_runtime.py:945-948`) ведёт в
  `_finish_with_report`. Там те же гейты честности, что у `final_answer`, затем
  `_build_report_context` собирает JSON с фактами прогона (files_changed,
  files_read, errors, commands) и `_render_report` делает отдельный вызов
  быстрой модели с `report_prompt.txt`, ответ стримится пользователю.
- Конец хода сейчас: терминальное действие `final_answer`/`report_to_user`/`refuse`
  (`_ActionOutcome.finished`), в нативном режиме ещё ответ без вызовов
  (`agent_runtime.py:804-805`, превращается в `final_answer`). Плюс остановки:
  бюджет шагов, отмена, `_build_early_stop_message` (застой, серия ошибок).
- Гейты честности: `refresh_completion_requirements` →
  `_compute_completion_status` (`agent_runtime.py:3133`) и
  `_evidence_completion_blockers` (`agent_runtime.py:3226`): `no_changes`
  (задача меняет файлы, `files_changed` пуст), `errors_present` (progress 100 и
  ошибки при пустом `files_changed`), `no_activity`, незакрытый план,
  непроверенные правки, пропавшие артефакты, массовое удаление.
  `"outcome": "incomplete"` обходит гейты: это честный выход.

### Почему gpt-6-astra уходит в JSON-режим

Реальные вызовы API в этой задаче запрещены, поэтому ниже пути в коде, которые
ведут в JSON-режим. Подтвердить, какой именно срабатывает у astra, можно по
debug-строкам `[native tools] …` в логе прогона (`AGENT_VERBOSE=1`).

1. **Проба падает любой ошибкой.** `_probe_native_tools` (`agent_model.py:683-732`)
   ловит все исключения. Если текст ошибки не содержит `tool_choice`, проба
   возвращает `False`, и `_NATIVE_TOOL_SUPPORT` запоминает это до конца процесса
   (`agent_model.py:669-671`). Таймаут (`api_timeout` = 60 с,
   `agent_state.py:74`) или 429 на тяжёлой модели с `reasoning_effort`
   отключает нативный режим на весь процесс. Повтора нет.
2. **Проба без вызова.** Ответ без `tool_calls` или с другим именем → `False`
   (`agent_model.py:722-727`).
3. **Отказ посреди прогона.** `is_tools_rejection` (`agent_model.py:735-744`)
   считает отказом любую ошибку, где есть `tool` и `not support`/`unsupported`.
   Тогда `NativeToolsRejected` → `_leave_native_tools` → `forget_native_tools`
   (`agent_runtime.py:727-743`): JSON до конца процесса.
4. **Флаг.** `AGENT_NATIVE_TOOLS=0` (`agent_model.py:667`).
5. **Попутная находка.** Нативный запрос всегда шлёт
   `temperature = model_temperature` (0.1) (`agent_model.py:802`), а JSON-путь
   умеет снимать `temperature` после отказа (`_TEMPERATURE_UNSUPPORTED`,
   `agent_model.py:1436-1504`; тест `test_cached_json_mode_rejection_…` с
   `gpt-6-astra-regression` показывает, что astra такой отказ даёт). Проба идёт
   без `temperature` и проходит, а каждый нативный ход падает на
   «Unsupported value: 'temperature'». В JSON это не переключает, но прогон
   умирает на серии ошибок. Не чиню, по условию фазы.

### `preexec_fn` и Windows

- Единственное место: `process_tree.run_supervised` (`process_tree.py:370-391`).
  На POSIX `preexec` в дочернем процессе до `exec` вступает в cgroup-scope
  (`_CgroupScope.join_current_process`) и ставит rlimits
  (`terminal._unix_resource_limits`, `terminal.py:3926-3950`, передаётся из
  `terminal.py:1091-1104`).
- На Windows `preexec_fn` не передаётся: вместо него
  `creationflags = CREATE_NO_WINDOW` (`process_tree.py:387-388`), rlimits нет
  (`rlimit_setter = None`), cgroup нет, убийство дерева best-effort через
  `proc.kill()` (`process_tree.py:294`, `501`).
- Важно для параллельности: документация Python прямо предупреждает, что
  `preexec_fn` небезопасен в процессе с потоками. Через `run_supervised` идут
  `run_shell`, `git_status`, `git_diff`. `search_code` запускает `rg` напрямую
  через `subprocess.Popen` без `preexec_fn` (`executor.py:1716`).

### Промпты, где есть `thought`, `user_message` или 140

- Системные: `coder_prompt.txt` (3, 29, 31, 185, 278-305), `devops_prompt.txt`
  (3, 34, 36, 170, 222-246), `reader_prompt.txt` (3, 22, 24, 94-98),
  `native_tools.txt` (строка про user_message во втором абзаце).
- Стили агентов, все девять: `architect.txt:67`, `default.txt:61`,
  `explainer.txt:60`, `fast-and-dirty.txt:63`, `friendly-developer.txt:37,46`,
  `minimalist.txt:62`, `paranoid.txt:66`, `perfectionist.txt:65`,
  `senior-dev.txt:74`. Везде это абзац «The user_message field during work: …».
- Стили чата (`prompts/styles/chat/*`) и `chat_prompt.txt`, `planner_prompt.txt`,
  `report_prompt.txt`, `router_prompt.txt`, `commit_prompt.txt`,
  `constitution.txt`: упоминаний нет.

## Как будет

Пометка **[агент]** значит «решение агента, требует ревью»: владелец этот случай
не покрыл, выбор сделан по ходу работы.

### Формат ответа

Ответ модели в обоих режимах: свободный текст человеку и ноль или больше вызовов.
Полей `thought`, `user_message`, `plan_step`, `step_summary`, лимита 140 символов
больше нет. Ноль вызовов = конец хода, текст = ответ.

**Нативный режим.** Текст идёт в `content`, вызовы в `tool_calls`. Инструменты
строятся из списка `ALLOWED ACTIONS` ролевого промпта, как сейчас.

**Текстовый режим** (нет нативного tool calling, главный путь gpt-6-astra):

```
Нашёл два места, где роль выбирается заново. Читаю оба.
<tool_call>{"name": "read_file", "arguments": {"path": "role_router.py"}}</tool_call>
<tool_call>{"name": "search_code", "arguments": {"query": "role", "path": "src/"}}</tool_call>
```

Разбор (`parser.parse_model_response`):

- тег ищется в любом месте текста, регистр и пробелы внутри тега не важны;
- вокруг тега или внутри него допускается markdown-обёртка ```` ```json ````;
- незакрытый тег: JSON читается до конца объекта, следующий `<tool_call>` тоже
  считается концом;
- ключи вызова: `name` (или `tool`, `type`), аргументы `arguments` (или `args`,
  `parameters`, `input`); `arguments` строкой с JSON тоже принимается;
- «умные» кавычки и висячие запятые чинятся тем же кодом, что сейчас;
- невалидный вызов не роняет ход: он становится вызовом с ошибкой, модель
  получает её текст на месте этого вызова, остальные вызовы выполняются;
- текст = всё вне тегов, с обрезанными краями.
- **[агент]** Ответ, который целиком является одним JSON-объектом старого
  контракта (`actions`/`action`), читается как вызовы, `user_message` из него
  становится текстом, `thought`/`plan_step`/`progress`/`step_summary`
  игнорируются. Это страховка для модели с закэшированным старым промптом.
  JSON-режим провайдера (`response_format: json_object`) для хода выключается,
  иначе теги были бы невозможны. Планировщик JSON-режим сохраняет.

**[агент] Текстовый режим остаётся на пересборке промпта.** Каждый шаг промпт
строится заново из `RunState` (`agent_context.build_runtime_prompt`), как сейчас в
JSON-режиме. Переводить его на растущий разговор, как у нативного режима, не стал:
отлаженные механизмы (приоритеты секций, окно деталей, repeat guard) завязаны на
пересборку, и такой переезд удвоил бы риск ночной работы. Цена: модель видит свои
прошлые слова только через заметки шагов (см. ниже).

**[агент] Память между шагами.** Текст ответа становится заметкой шага
(`rolling_summaries`, секция «Step summaries»). Это замена `step_summary`: модель
уже пишет человеку то, что выяснила. Нативный режим и так держит текст в
разговоре.

**[агент] `plan_step` выводит ядро** из имён вызовов, как сейчас делает нативный
режим (`native_tools._plan_step`): запись → `edit`, shell после правок →
`verify`, иначе `inspect`, конец хода → `done`.

**[агент] `final_answer` и `report_to_user`.** Модели они больше не предлагаются
(нет в `ALLOWED ACTIONS`, нет в инструментах). Если модель их всё же вызовет
(старый промпт), ядро выполнит их как раньше: явный финиш с `outcome` и, для
`report_to_user`, рендер отчёта. Удалить репортер и эти действия совсем стоит
отдельным шагом после ревью. `refuse` остаётся инструментом.

### Подпись вызова

Ядро строит подпись из аргументов (`call_labels.call_label`), по языку ответа:
«читает src/x.py», «ищет "role" в src/», «правит a.py», «запускает pytest».
Подпись идёт в `Action.description`, в `step.action_description`, в строку
статуса. `run_shell` получает от модели обязательный `purpose` (до 10 слов,
длиннее обрезается, не ошибка); подпись shell = `purpose`. `purpose` убирается
из аргументов до исполнителя.

### Реестр инструментов и параллельность

Реестр `native_tools.ACTION_TOOLS` получает флаг `readonly`. `readonly=True`:
`read_file`, `read_multiple_files`, `list_dir`, `list_tree`, `find_files`,
`search_code`, `web_search`, `fetch_url`. Всё остальное пишущее, `run_shell`
всегда.

**[агент]** `git_status`/`git_diff` не `readonly`: их нет в списке владельца, и
они идут через `process_tree.run_supervised` с `preexec_fn`, который Python не
гарантирует в процессе с потоками. Поэтому они выполняются по очереди и
пропускаются после упавшей записи, как пишущие.

Исполнение пачки (`agent_runtime._run_calls`):

1. Берутся первые 8 вызовов. Остальные получают ответ «не выполнено: не больше
   8 вызовов за ответ».
2. Пачка режется на отрезки: подряд идущие `readonly` вызовы образуют группу,
   каждый пишущий вызов стоит отдельно. Порядок отрезков = порядок вызовов,
   поэтому чтение после записи в одной пачке видит запись.
3. Группа чтений: проверки (guard'ы, язык, бюджет web) идут по очереди, затем
   исполнитель (`SafeExecutor.execute`) вызывается в `ThreadPoolExecutor` на 4
   потока, затем результаты регистрируются по очереди в порядке вызовов. Всё,
   что меняет `RunState`, остаётся в главном потоке.
4. Пишущий вызов идёт через `_run_one_action`, как сейчас: подтверждение
   (HITL) для каждого отдельно, по очереди, в главном потоке. Кнопки «одобрить
   все» нет.
5. Ошибка чтения ничего не отменяет. Упавшая или заблокированная запись
   помечает пачку: следующие пишущие вызовы не выполняются и получают ответ
   «Skipped: an earlier write in this response failed.» (в интерфейсе
   «пропущено: предыдущая запись упала»). Чтения после неё выполняются.
   **[агент]** Невалидный вызов (не разобрался JSON или аргументы) считается
   упавшей записью, если его имя не `readonly` или не разобралось: безопаснее
   не делать следующую запись, которая могла от него зависеть.
6. **[агент]** `refuse` рядом с другими вызовами: выполняется только отказ,
   остальное не выполняется. Безопасность важнее пачки.
7. Бюджет шагов: каждый вызов остаётся своим шагом. Пропущенный вызов тоже
   получает номер шага, чтобы интерфейс мог показать его в группе.

Результаты возвращаются модели в порядке вызовов:

- нативный режим: `tool`-сообщение на каждый вызов, в порядке вызовов;
- текстовый режим: секция «Detailed recent action data» в порядке регистрации,
  то есть в порядке вызовов.

Каждый результат обёрнут отдельно:
`<UNTRUSTED_TOOL_OUTPUT source="read_file" call_id="…">…</UNTRUSTED_TOOL_OUTPUT>`.
Закрывающий тег внутри результата обезвреживается существующим
`redaction.defang_prompt_boundary_tags`.

Guard массового удаления (`_is_destructive_diff`, `_flag_removed_security_check`)
и все guard'ы `executor.py` не меняются.

Искусственная задержка `_run_pipeline_dwell` удаляется вместе с предзагрузкой,
которую она обслуживала. Ограничение частоты запросов из `/settings-api` остаётся:
это настройка пользователя, не задержка ради показа.

### Цикл хода

```
loop:
  ответ модели → (текст, вызовы)
  вызовов нет:
     пустой текст → ошибка «пустой ответ», следующий шаг
     иначе → конец хода: гейты честности (ниже)
  вызовы есть:
     текст → заметка шага, user_messages, событие step первого вызова
     _run_calls(...) → результаты в порядке вызовов
     чекпоинт
```

### Новый сигнал конца хода и проверки честности

Конец хода = ответ без вызовов. Текст превращается во внутреннюю команду
`final_answer` и идёт через `_finish_with_final_answer`: язык ответа, гейты
`completion_blockers`, `missing_artifacts`, `files_changed`, план,
непроверенные правки, пустые ячейки данных, удалённые проверки безопасности,
существенность ответа для задач-отчётов.

Правило владельца (после ревью): если `PlanInfo.task_kind` говорит, что
результат задачи — изменение файлов, «готово» при пустом `files_changed`
отклоняется всегда, что бы ни вернул последний вызов. Для вопросов и чтения
правило не применяется.

Реализация:

- у `task_kind` третье значение `change` (было только `report | action`, и
  `action` покрывал и «прогони тесты», поэтому он изменения файлов не
  подразумевал). Планировщик ставит `change`, когда результат — создать,
  править, переименовать или удалить файлы, в том числе если пользователь
  ещё просит объяснение (раньше такой случай шёл в `report`). Эвристика
  `infer_task_kind` для провала планировщика: глагол изменения
  (`role_router.has_change_verb`) или маркер создания → `change`, иначе маркеры
  отчёта → `report`, иначе `action`;
- блокер `no_changes` в `_evidence_completion_blockers` при `task_kind ==
  "change"` и пустом `files_changed` ставится без исключений. Угадывание по
  ключевым словам (`create_goal`, `modify_goal`, план с глаголами правки)
  сохраняет прежние исключения для read-only и verify-only прогонов;
- `errors_present` вернулся к виду из `main`;
- `report_goal` (проверка содержательности ответа) для `change` берётся по
  маркерам объяснения в запросе, чтобы «исправь и объясни» всё ещё требовал
  объяснения.

**[агент] Честный выход без `outcome`.** Первый конец хода, который ядро не
приняло (гейты завершения или guard корректирующего действия), отправляет модели
причины и подсказку «продолжи работу вызовами или, если работу нельзя сделать,
закончи ход ещё раз и прямо скажи, что не сделано и почему». Следующий конец хода
в этом прогоне, если блокеры всё ещё есть, принимается как незавершённый:
`outcome: "incomplete"` ставит ядро, `finished_incomplete = True`,
`run_status = blocked`, событие `final` получает `outcome: "incomplete"`, к
тексту добавляется одна строка ядра: «_Kvist: задача не отмечена как выполненная
(причины)._» (метки из `_BLOCKER_LABELS`). Если блокеров уже нет, конец хода
идёт обычным путём. Так же ведёт себя старый `final_answer` без `outcome`.
Счётчик отказов (`RunState.finish_refusals`) за прогон не сбрасывается.

### События

Всё добавочно, `protocol_version` остаётся 1.

| Событие | Было | Стало |
|---|---|---|
| `step` | `thought`, `user_message`, `action_description` | + `call_id`, `batch_id`. `thought` всегда `""`. `user_message` = текст ответа на первом `step` пачки, `""` на остальных. `action_description` = подпись ядра |
| `action_result` | — | + `call_id`, `batch_id` |
| `action_output` | — | + `call_id` |
| `status` | `user_message` = фраза модели | `user_message` = подпись текущего вызова |
| `final` | `message`, `user_messages` | + `outcome`: `"done"` или `"incomplete"` |

Старые фронтенды продолжают работать: ключи не пропали, `user_message` на первом
шаге пачки показывается так же, как раньше показывалась фраза шага.

Для пропущенного вызова ядро шлёт `step` и `action_result` с `ok: false` и
сводкой «пропущено: предыдущая запись упала» / «skipped: an earlier write
failed».

### План миграции потребителей

| Потребитель | Что меняется |
|---|---|
| `protocol.py` | `AgentCommand`: убрать `thought`, `user_message`, `step_summary`; добавить `text`, `call_id`, `batch_id` |
| `parser.py` | новый `parse_model_response` (теги + старый объект), `build_command` на один вызов; `parse_agent_commands` удаляется; `run_shell` принимает `purpose` |
| `native_tools.py` | реестр с `readonly`, `purpose` у `run_shell`, без синтеза `thought`/`user_message`, ошибки по вызову |
| `call_labels.py` (новый) | подписи вызовов RU/EN |
| `agent_runtime.py` | `_run_calls` вместо `_run_action_batch`/`_run_native_batch`; конец хода; `_note_thought` → текст ответа; `update_live` с подписью; логи шагов с `text`/`call_id`/`batch_id`; `_build_plan_evidence_text` берёт текст и подпись; удалить `_run_pipeline_dwell` |
| `agent_model.py` | `call_model(json_mode=False)` для хода; удалить предзагрузку; verbose-печать без `"thought"` |
| `agent_web.py` | повтор `web_search` сравнивается с текстом ответа вместо `thought` |
| `agent.py` | синтетическая команда `/resume` без `thought`/`user_message` |
| `agent_context.py` | языковые правила про текст ответа вместо `user_message` |
| `action_result.py`, `frontend_protocol.py`, `event_stream_ui.py`, `session_events.py` | `call_id`, `batch_id`, `outcome` |
| `terminal_banner.py`, `banner.py` (classic) | текст ответа печатается в ленту один раз на пачку; колонка мышления берёт текст ответа; verbose без `Thought:`/`Message:` |
| `src/protocol.ts`, `src/state.ts` | новые поля; `say` из текста первого шага пачки, в том числе для чтений; несколько вызовов в полёте по `call_id`; группа пачки |
| `src/ui/*`, `vscode-extension/webview/*` | отрисовка группы пачки |
| `gui/renderer/app.js` | карточки шагов одной пачки в общем блоке |
| промпты | формат ответа в `coder/devops/reader`, `native_tools.txt`, абзацы про `user_message` во всех стилях агентов; стили чата не описывают формат, не меняются |
| `UI_API.md`, `UI_API.ru.md` | новые поля |
| jetbrains-plugin | не трогается (отдельная ветка, событий не читает) |
| тесты | `conftest.make_command`, тесты парсера, рантайма, событий, фронтендов |

## Следующие шаги

- Удалить `final_answer` и `report_to_user` целиком: из `protocol.ActionType`,
  парсера, реестра инструментов, `_finish_with_report` / `_render_report`,
  `report_prompt.txt` и тестов. Сейчас модели они не предлагаются, но ядро их
  ещё принимает от модели со старым промптом.
