# BESTOF_PROGRESS — best-of, council, арена, thinking, кэш

Рабочий журнал. Новая сессия продолжает работу **только по этому файлу**.
Пишется по-русски, без английского двойника: это журнал работы, а не
пользовательская документация (прецедент — `docs/audits/`, `docs/reports/`).

## 0. Как продолжить (для новой сессии)

1. `git -C <repo> log --oneline -15`, `git worktree list`, `git branch --list 'bo/*'`
   (`<repo>` — корень основного checkout, рабочая директория сессии).
2. Прочитать §5 (задачи и статусы), §6 (журнал ревью), §8 (следующий шаг). Самая свежая
   копия этого файла — в `<repo>/.claude/worktrees/int` (там же правится); в `experimental` —
   последняя закоммиченная.
3. Работа идёт так: задача → worktree `bo/<id>` → сабагент (sonnet) → ревью →
   слияние через `bo/int` (порядок ниже) → строка в §6 и статус в §4.
4. Живые прогоны: счётчик в §9. Лимит 10 на всю работу.

**Пути.** Корень: `<repo>` (ветка `experimental`, рабочее дерево
пользователя — его не трогать руками, только `merge --ff-only`). Worktree задач:
`<repo>/.claude/worktrees/<id>` на ветках `bo/<id>` от `experimental`
(папка `.claude/` в `.gitignore`). Интеграция: `<repo>/.claude/worktrees/int`
на ветке `bo/int`.

**Тесты в worktree** (в venv стоит editable-установка, указывающая на основной checkout):
```bash
cd <wt> && PYTHONPATH=<wt> <repo>/.venv/bin/python -m pytest -q -p no:cacheprovider
cd <wt> && <repo>/.venv/bin/python -m ruff check .
# session-rs: свой target внутри worktree (НЕ общий: лаунчер берёт release-бинарь основного checkout)
cd <wt>/kernel/session-rs && cargo test && cargo clippy --all-targets -- -D warnings && cargo build --release
# glass / tauri: общий target основного checkout, только debug-профиль, никогда --release
cd <wt>/frontends/tui/glass && CARGO_TARGET_DIR=<repo>/frontends/tui/glass/target cargo test && CARGO_TARGET_DIR=<repo>/frontends/tui/glass/target cargo clippy --all-targets -- -D warnings
cd <wt>/frontends/gui/tauri && node --test "tests/*.test.mjs"
```

**Слияние задачи** (одна задача за раз, красное не вливается):
```bash
INT=<repo>/.claude/worktrees/int
git -C $INT reset --hard experimental          # bo/int создана в этой работе
git -C $INT merge --squash bo/<id>             # конфликт → разбор, при необходимости rebase bo/<id>
# правка этого файла в $INT (§4 статус, §6 ревью), git -C $INT add -A
# полный прогон в $INT: pytest, ruff, cargo (если задача rust), node (если tauri)
git -C $INT commit                              # один коммит на задачу
git -C <repo> merge --ff-only bo/int   # experimental уходит вперёд на один коммит
```
`reset --hard`, `clean`, удаление веток/worktree — только для `bo/*` и их папок.

## 1. Состояние

- Отправная точка: `experimental` @ `eb0d067`, дерево чистое, worktree — только основной.
- Базовая линия (16.11, основной checkout): `pytest` — 5089 passed, 51 skipped, 218 deselected, 3:41;
  `ruff check .` — чисто.
- Ресурсы тесные: на дисках по ~20 ГБ свободно, RAM ~4 ГБ свободно; `target` glass и tauri —
  по 7.9 ГБ: параллельных полных rust-сборок не делать (см. D-19).
- Живых прогонов израсходовано: 0 из 10.

## 2. Сверка known_state с кодом

Все коммиты на месте: 4fafa12, 622b1a0, da98f46, cc7fffa, c1edd9f, 700224b, d345fe9,
edad255, d45484c, c3c1626, 8ee2fd4, 4325243, 326c4e1.

Долги — подтверждены:
- thinking снимается на шагах с tool_use: `kernel/llm/llm_clients.py` (`_replays_tool_use` →
  `del request_kwargs["thinking"]`). Подпись (`signature_delta`) и `redacted_thinking` вообще
  не читаются ни в потоке (`_normalize_stream_chunk`), ни без него (`_normalize_response`).
- итог за ход, а не за сессию: `kernel/session/session_events.py` (`_write_turn_summary`,
  `usage` с `kind: "turn"`); в документе сессии итогов нет.
- набор tools меняется посреди прогона: `kernel/plan/plan_mode.py:tools_for_phase` вызывается на
  каждом нативном шаге (`agent_runtime._call_native_turn`); скиллы/MCP добавляют tools при
  подключении (`skills.tool_definitions`).
- стиль перед инструкциями проекта: `kernel/agent/agent_model.py:build_role_system_prompt`
  (порядок: конституция, роль, slash, план, extensions, **стиль**, vision, KVIST.md, voice).
- у claude 5 в `model_prices.json` `max_output_tokens: null` (opus-5-5, sonnet-5-5, fable-5-2 и др.)
  → действует запасной 8192 (`llm_clients._FALLBACK_MAX_OUTPUT_TOKENS`).

Расхождения и уточнения:
1. «Сессии переписаны на rust» — на Rust только `--session-op` (`kernel/session-rs`). Документы
   сессий и журналы во время хода пишет Python (`agent_logging.py`, `session_events.py`);
   Rust их читает и сам пишет только маркер и документы для new/create/switch/delete/reset/launch.
2. Стили исполнителей уже есть: `kernel/prompts/styles/agents/{senior-dev,minimalist,paranoid,
   fast-and-dirty,perfectionist,architect,...}.txt`, файл начинается со строки `WORKING STYLE: <name>`.
3. Нативная история (`RunState.native_messages`) в чекпоинт не попадает; `/resume` строит
   разговор заново из runtime-промпта.
4. Текстовый режим шлёт `[system, user]`; anthropic получает одну метку (system). Нативный —
   три (system, последний tool, последнее сообщение).
5. `chatgpt_client._prompt_cache_key` хеширует instructions целиком + tools: разные стили →
   разные ключи → разная маршрутизация → общего кэша между исполнителями нет.
6. Нативные сообщения проходят `redact_value` перед каждым запросом
   (`agent_model._request_native_turn`) — это сломает подписи thinking.
7. Системный промпт текстового режима строится на каждом вызове (`call_action_model`) и
   зависит от `plan_mode.offer_available`/`plan_phase`, KVIST.md (Kvist сама его правит) и
   файлов стилей, читаемых на каждом вызове — проверка и исправление в T10.
8. `.kvist/` в `.gitignore` пользовательских проектов не добавляется (Kvist добавляет только
   KVIST.md/KVIST-CHAT.md). KVIST.md в worktree поэтому отсутствует (см. D-11).
9. Помощники (`kernel/agent/helpers.py`) — готовый образец: отдельный `LocalAgent` на потоке,
   служебная сессия `service={kind, parent_session, ...}`; Python и Rust уже исключают такие
   сессии из маркера активной и из `resumable_session`.
10. В venv editable-установка `kvist` (finder на основной checkout): тесты в worktree — только с
    `PYTHONPATH=<wt>` (D-20).

## 3. Решения (с причинами)

- **D-01 Worktree сабагентов.** `.claude/worktrees/<id>`, ветки `bo/<id>` от `experimental`.
  CLAUDE.md запрещает другие ветки без просьбы — просьба есть (orchestration). Папка внутри
  рабочей директории и в `.gitignore`: нет запросов прав и шума в `git status`.
- **D-02 Слияние** через `bo/int` (squash, полный прогон, один коммит, `merge --ff-only`):
  на `experimental` нет ни reset, ни clean, красное туда не попадает.
- **D-03 Прогресс-файл** по-русски без двойника (журнал, см. шапку).
- **D-04 Стили исполнителей** = штатные agent-стили, лестница:
  `senior-dev, minimalist, paranoid, fast-and-dirty, perfectionist, architect`; N берёт первые N
  (2 → senior-dev, minimalist; 4 → как в ТЗ). Для 5 и 6 — perfectionist и architect: другие
  штатные стили с отличной от первых четырёх установкой.
- **D-05 Стиль в конце системного промпта для всех прогонов.** Новый порядок: конституция, роль,
  slash, план, extensions, vision, инструкции проекта, `## RESPONSE STYLE` + common.txt, затем
  текст выбранного стиля (+ акцент для chat). Секция voice mode остаётся после стиля: она
  существует, чтобы перевешивать правила стиля (комментарий в коде). Граница «общая часть /
  стиль» — явная константа-маркер в нейтральном модуле, её используют chatgpt и anthropic.
- **D-06 Общий префикс исполнителей** = tools + системный промпт до границы. Текст задачи
  байт в байт одинаков, но стоит после расхождения (anthropic рендерит tools → system →
  messages, стиль — в system). Это буквальное «стиль в самом конце системного промпта»;
  текст задачи короткий, общая часть — десятки тысяч токенов.
- **D-07 Кэш.** chatgpt: `prompt_cache_key` из instructions до границы + tools → общая
  маршрутизация исполнителей (session-заголовки выводятся из ключа). anthropic: system из двух
  блоков с меткой на каждом; метки: последний tool, общая часть system, конец system,
  последнее сообщение — ровно 4.
- **D-08 Tools постоянны весь прогон.** Набор считается один раз в начале прогона; ограничения
  фазы плана — отказом (`plan_mode.rejection_reason` и запрет записи в planning), не скрытием.
  offer_plan/propose_plan в наборе, если прогон вообще может войти в план. Исключение:
  скилл/MCP, подключённый посреди прогона, дописывает свои tools в конец — модель не вызовет
  невидимый инструмент, а общий диспетчер скиллов поменял бы протокол скиллов для всех
  прогонов (больше, чем описано в stream_a).
- **D-09 Грязное дерево.** Старт разрешён. Варианты стартуют со снимка S = HEAD + отслеживаемые
  правки + неигнорируемые новые файлы (без `.kvist/`), собранного через временный индекс;
  индекс и файлы пользователя не меняются. Принятие: в коммит идут **только изменения
  победителя** (diff S→W, наложенный на текущий HEAD через временный индекс), один коммит в
  ветке пользователя; свои незакоммиченные правки пользователя остаются незакоммиченными.
  Конфликт = победитель меняет путь, который пользователь менял после старта, или патч не
  ложится на HEAD (задевает его незакоммиченные строки) → остановка, показ путей и сообщения
  git, ничего не меняется; после коммита/stash пользователь повторяет accept. Отказ на старте:
  не git, нет коммитов, detached HEAD, идёт merge/rebase/cherry-pick.
- **D-10 Раскладка.** `<ws>/.kvist/bestof/<run_id>/<executor_id>`, ветка
  `kvist/bestof/<run_id>/<executor_id>`, состояние `<ws>/.kvist/bestof/<run_id>/run.json`
  (pid и время старта владельца). Восстановление: владелец мёртв → `interrupted`, worktree
  остаются для просмотра; `git worktree prune`; пропавшая папка → исполнитель `lost`.
- **D-11 Исполнитель** = LocalAgent как помощник, но в своём worktree: служебная сессия
  `service={kind:"executor", parent_session, run_id, executor_id, style, branch}`; документ и
  журнал его сессии лежат в workspace родителя (корень сессий ≠ рабочая папка); KVIST.md
  читается из workspace родителя, шаблоны в worktree не создаются. Перезапись файлов в своём
  worktree без вопроса (он одноразовый); опасные команды — через шлюз основного агента, если он
  отвечает с любого потока, иначе отказ.
- **D-12 Судья** = агент основной сессии в ходе best-of, внутренняя роль `judge` (роутер её не
  выбирает), только чтение пользовательского workspace; tools: чтение + `executor`
  (start, status, wait, inspect, message, diff, check, eliminate) + `verdict`. Слово «судья» —
  только в коде, логах и промпте.
- **D-13 Решение человека** — `input_request` `choice` с аддитивными полями
  `purpose: "bestof_decision"`, `run_id`. Варианты: `accept`, `diff`, `other`, `discard`; ядро
  также принимает `diff:<executor_id>` и `pick:<executor_id>` (для экранов вердикта). Текст
  вердикта — в `body` первого диалога, diff — в `body` следующего и событием `bestof_diff`.
  Закрытый диалог → прогон `pending`, потом `/best-of accept|diff|discard [id]`.
- **D-14 Команды-ходы** `/best-of` и `/council`: запуск сообщением `command` или строкой цели с
  этим префиксом; поток хода, затем `turn_end`, затем `command_result` (совместимость с
  фронтендами, которые ждут `command_result`: Ink, glass). Аудит фронтендов — задача T13.
- **D-15 Council.** 4 участника — read-only агенты роли reader, служебные сессии
  `kind:"council_member"`, `executor_id` = угол: `simplicity, security, speed, maintenance`.
  Раунд 1 — карточка JSON (решение, главный довод); раунд 2 — главный риск каждого чужого
  решения. Судья — один вызов модели без tools: `chosen`, `reason`, `dissent`. Текст решения в
  вердикте код копирует из выбранной карточки — усреднение невозможно по построению.
- **D-16 Итоги за сессию.** `usage_totals` в документе сессии, копятся на каждом вызове модели;
  элементы `--session-op status/list` несут `usage` (своё) и `usage_total` (своё + потомки по
  `service.parent_session`); `reset` обнуляет; паритет Rust.
- **D-17 Thinking.** Блоки (thinking+signature, redacted_thinking.data) → NativeTurn → ключ на
  assistant-сообщении с меткой provider+model → повторная отправка дословно (без redact) только
  тому же provider+model; другим провайдерам/моделям ключ не уходит. На диск — в редактированном
  виде с дайджестом; блок, чей дайджест не сошёлся, при отправке выбрасывается. Детали — T11.
- **D-18 Экраны.** glass/pixel рисуют арену из `bestof_*`, лог исполнителя — из
  `--session-op history` его сессии; деньги: `cost_usd` (≈ при `cost_approx`), иначе всего
  токенов и некэшированных; `tool_mode` в статусной строке.
- **D-19 Rust.** session-rs — свой target в worktree; glass/tauri — общий target основного
  checkout, только debug (test/clippy), без `--release`. Две rust-задачи одного крейта
  одновременно не идут.
- **D-20 Тесты в worktree** — только с `PYTHONPATH=<wt>` (editable finder в venv).
- **D-21 Потолок вывода claude 5** не правится: источника в репозитории нет, выдумывать нельзя.
  В финальном отчёте как оставшийся долг.
- **D-22 Брифы** лежат в `<repo>/.claude/bestof-briefs/` (вне git: в них абсолютные пути машины,
  а репозиторий публичный — правило low-profile privacy).
- **D-23 Git внутри песочницы исполнителя недоступен — и так и остаётся.** У git worktree файл
  `.git` указывает в `.git/worktrees/<name>` основного репозитория, вне workspace исполнителя;
  `terminal_git._git_metadata_directory` на это отвечает жёстким отказом (защита от выхода из
  workspace), а bwrap основной `.git` не монтирует. Ослаблять проверку нельзя: исполнитель с
  записью в основной `.git` мог бы подложить хук/конфиг пользователю. Рассмотренные замены:
  `git clone --local` (жёсткие ссылки на объекты → исполнитель может испортить объекты основного
  репозитория через общий inode), `--no-hardlinks` (копия всего `.git` на каждого исполнителя),
  `--shared` (нужен ro-bind объектов — правка песочницы). Выбран git worktree по ТЗ: diff, итог и
  коммиты делает оркестратор вне песочницы; в тексте задачи исполнителя сказано, что git в его
  папке недоступен, а изменения записывает среда.

## 4. Контракт событий v1 (аддитивно, protocol_version = 1)

Общее: каждое событие дочерней сессии (исполнитель best-of, участник council), записанное в её
журнал, несёт `executor_id`. В основной поток события исполнителей не идут — только сводки ниже.

| type | поля | журнал |
|---|---|---|
| `bestof_started` | `run_id, task, count, base` (короткий хеш S), `executors:[{executor_id, style, branch, session}]` | да |
| `bestof_status` | `run_id, executor_id, style, branch, state` (`waiting/running/finished/failed/stopped/eliminated/lost`), `step, tests:{state:"unknown"\|"running"\|"passed"\|"failed", summary}, diff:{files, added, removed}, usage:{input_tokens, output_tokens, cache_read_tokens?, cost_usd?, cost_approx}` — снимок, заменяет прошлый | нет |
| `executor_activity` | `run_id, executor_id, phase:"step"\|"result", call_id, action_type, label, ok?, summary, output` | нет |
| `bestof_eliminated` | `run_id, executor_id, reason` (на языке человека) | да |
| `bestof_verdict` | `run_id, rows:[{executor_id, style, outcome:"winner"\|"finalist"\|"eliminated"\|"failed", tests, diff:{files,added,removed}, note}], winner, reason` (2–3 предложения) | да |
| `bestof_diff` | `run_id, executor_id, patch` (обрезан, редактирован), `files:[...]` | нет |
| `bestof_finished` | `run_id, outcome:"accepted"\|"discarded"\|"cancelled"\|"failed"\|"pending", executor_id?, commit?, message` | да |
| `council_started` | `council_id, question, members:[{executor_id, angle, session}]` | да |
| `council_card` | `council_id, executor_id, angle, decision, argument` | да |
| `council_objection` | `council_id, executor_id, angle, risks:[{target, risk}]` | да |
| `council_verdict` | `council_id, chosen, decision, reason, dissent:[{executor_id, angle, why}]` | да |

`input_request` получает необязательные `purpose` и `run_id` (D-13). Подписи в интерфейсе:
«best-of» и «council», рядом одна строка объяснения; слова «судья» в интерфейсе нет.

## 5. Задачи

Статусы: `todo`, `brief`, `work`, `review`, `rework-N`, `merged <hash>`, `split`.

| id | поток | цель | владеет файлами | зависит | статус |
|---|---|---|---|---|---|
| T1 | a2 | менеджер worktree | `kernel/bestof/__init__.py`, `kernel/bestof/worktrees.py`, `kernel/tests/test_bestof_worktrees.py` | — | work |
| T2 | a3 | события и протокол | `kernel/protocol/frontend_protocol.py`, `kernel/protocol/event_stream_ui.py`, `kernel/protocol/ui.py`, `kernel/session/session_events.py`, `docs/UI_API.md`, `docs/UI_API.ru.md`, тесты этих модулей | — | work |
| T3 | a1 | сессии исполнителей, итоги за сессию | `kernel/session/agent_logging.py`, `kernel/session/session_store.py`, `kernel/session/session_usage.py` (новый), `kernel/agent/agent.py` (только конструктор), `kernel/agent/agent_model.py` (только `_emit_usage`), `kernel/session-rs/src/*`, тесты сессий | — | work |
| T4 | e1 | glass: арена, лог, вердикт, council, статус | `frontends/tui/glass/**` | контракт §4 | work |
| T5 | a4 | префикс кэша, стиль в конце, постоянные tools | `kernel/agent/agent_model.py` (промпт), `kernel/plan/plan_mode.py`, `kernel/agent/agent_runtime.py`, `kernel/providers/chatgpt_client.py`, `kernel/llm/prompt_prefix.py` (новый), тесты | T3 | todo |
| T6 | b | исполнители (запуск со сдвигом, статус, выбывание) | `kernel/bestof/executors.py`, `kernel/bestof/styles.py`, тесты | T1 T2 T3 | todo |
| T7 | c | council + проводка команд-ходов | `kernel/bestof/council.py`, `kernel/prompts/bestof/*`, `kernel/protocol/event_stdio.py`, `kernel/protocol/slash_commands.py`, `kernel/protocol/core_manifest.py`, тесты | T2 T3 | todo |
| T8 | b | судья, прогон best-of, вердикт, принятие, `/best-of` | `kernel/bestof/run.py`, `kernel/bestof/judge.py`, `kernel/prompts/system/judge_prompt.txt`, `kernel/protocol/protocol.py`, `kernel/protocol/parser.py`, `kernel/agent/native_tools.py`, `kernel/agent/agent_runtime.py`, `event_stdio.py`, `slash_commands.py`, тесты | T5 T6 T7 | todo |
| T9 | b | предложение режима основным агентом | `protocol.py`, `parser.py`, `native_tools.py`, `agent_runtime.py`, ролевые промпты, `kernel/bestof/offer.py`, тесты | T8 | todo |
| T10 | d2 | аудит кэша anthropic, текстовый режим | `kernel/llm/llm_clients.py`, `kernel/agent/agent_model.py`, `kernel/agent/agent_context.py`, тесты | T5 | todo |
| T11 | d1 | thinking с подписью по всей цепочке | `kernel/llm/llm_clients.py`, `kernel/llm/anthropic_wire.py`, `kernel/agent/native_tools.py`, `kernel/agent/agent_model.py`, `kernel/session/agent_logging.py`, `kernel/session-rs` (если нужно), тесты | T9 T10 | todo |
| T12 | e2 | pixel gui: те же экраны | `frontends/gui/tauri/ui/**`, `frontends/gui/tauri/tests/**`, README | T4 T2 | todo |
| T13 | e3 | остальные фронтенды не ломаются, текстовый итог | по итогам аудита | T7 T8 | todo |
| T14 | — | живая проверка (сам) | — | T5 T7 T8 T10 | todo |

Волны: 1 — T1 T2 T3 T4; 2 — T5 T6 T7; 3 — T8 T10 (+T12); 4 — T9 T13; 5 — T11; затем T14.
Одновременно не больше 4 сабагентов; задачи с общим файлом идут друг за другом.

### Критерии приёмки (кратко; полные — в брифах §7)

- **T1**: снимок S не меняет индекс и файлы пользователя (проверка хешем индекса и mtime);
  worktree и ветки создаются/удаляются; без git — понятный отказ EN/RU; отказ на detached HEAD,
  пустом репо, merge в процессе; сводки diff --stat/файлы/+−/полный diff; принятие — ровно один
  новый коммит, только изменения победителя, пользовательские незакоммиченные правки остаются;
  конфликт — ничего не меняется и возвращаются пути и текст git; восстановление после «падения»
  (мёртвый pid) и `worktree prune`; `.kvist/` не попадает в снимок.
- **T2**: dataclass'ы событий §4, методы UI, политика журнала (табл. §4), штамп `executor_id`
  в журнале дочерней сессии, `purpose`/`run_id` на `input_request`, UI_API.md и .ru.md.
- **T3**: kind `executor`/`council_member` с родителем, корень сессий отдельно от рабочей папки,
  `usage_totals` за всю сессию, `usage`/`usage_total` в status/list (Python = Rust, паритет
  `test_session_native.py` на собранном крейте), reset обнуляет.
- **T4**: экраны арены/лога/вердикта/council по фикстурам событий, cost≈/токены, tool_mode;
  `cargo test` и `cargo clippy -D warnings` зелёные.
- **T5**: стиль последним (кроме voice), граница; два агента с разными стилями и worktree дают
  байт-в-байт одинаковую общую часть; tools одинаковы во всех фазах плана; отказы вместо
  скрытия; ключ кэша chatgpt одинаков для разных стилей.
- **T6**: сдвиг старта (второй стартует только после первого ответа первого), одинаковый текст
  задачи, стиль применён, события с `executor_id`, выбывание останавливает сессию, сессии в
  workspace родителя.
- **T7**: 4 угла, изоляция участников, 2 раунда максимум, выбор ровно одного, dissent, текстовый
  итог для фронтендов без экранов, команда-ход по D-14.
- **T8**: порядок обман → тесты → качество в промпте и в данных; ранние выбывания; полный diff
  только у финалистов; диалог решения D-13; принятие одним коммитом; отмена останавливает всех,
  сделанное доступно.
- **T9**: предложение только с согласия; текст без внутренних терминов, во сколько раз дороже,
  деньги/токены только из измеренного.
- **T10**: меток ≤ 4 на всех путях; system текстового режима байт в байт между шагами.
- **T11**: цепочка thinking по D-17, чужие блоки не уходят, снятие thinking убрано.
- **T12**: экраны pixel по тем же событиям, существующие экраны не меняются.
- **T13**: каждый фронтенд переживает новые события и команды-ходы; итог виден текстом.

## 6. Журнал ревью

(пусто)

## 7. Брифы

Брифы целиком — `<repo>/.claude/bestof-briefs/{common,<id>}.md` (D-22); сабагент получает
те же файлы. Сабагенту даются: `common.md` (hard rules целиком, конвенции, формат отчёта) и
бриф задачи (цель, разрешённые/запрещённые файлы, решения, тесты, критерии приёмки).

## 8. Следующий шаг

Волна 1 разослана (T1–T4 в работе, sonnet, по worktree). Бриф T5 готов. Дальше: ревью отчётов
T1–T4 по мере прихода, слияние по одному; после T3 — старт T5; после T1–T3 — брифы T6, T7.

## 9. Живые прогоны (лимит 10)

| # | что | результат |
|---|---|---|

## 10. Открытые вопросы

- Как именно каждый фронтенд обрабатывает команду, которая запускает ход (прецедент `resume`:
  Ink различает `resume`, glass ждёт `command_result`) — T13.
- Можно ли исполнителю запускать `git status` в своём worktree внутри bwrap (gitdir основного
  репозитория вне worktree) — проверяется в T6.

## 11. Ручные проверки на живом API (копится)

(пусто)
