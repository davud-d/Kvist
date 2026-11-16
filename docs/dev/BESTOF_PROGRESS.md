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

**В `experimental` коммитит и владелец** (его собственная сессия Kvist в основном checkout, например
`ca968db feat(dashboard)` в `cloud/frontend` 16.11). Поэтому перед `merge --ff-only` проверять
`git merge-base --is-ancestor experimental bo/int`; если нет — `git -C $INT reset --hard experimental`,
`git -C $INT cherry-pick <коммит задачи>`, полный прогон заново, потом перенос. Основной checkout
не трогать ничем, кроме `merge --ff-only`.

## 1. Состояние

- Отправная точка: `experimental` @ `eb0d067`, дерево чистое, worktree — только основной.
- Базовая линия (16.11, основной checkout): `pytest` — 5089 passed, 51 skipped, 218 deselected, 3:41;
  `ruff check .` — чисто.
- Ресурсы тесные: на дисках по ~20 ГБ свободно, RAM ~4 ГБ свободно; `target` glass и tauri —
  по 7.9 ГБ: параллельных полных rust-сборок не делать (см. D-19).
- Живых прогонов израсходовано: 2 из 10 (§9). Все задачи T1–T15 слиты; работа завершена (§8).
- Инцидент: все четыре сабагента волны 1 остановились на 5-часовом лимите использования
  аккаунта (HTTP 429 у сабагентов, это не лимит живых прогонов Kvist). Незакоммиченная работа
  осталась в их worktree; после сброса лимита они возобновлены с места остановки. Если лимит
  срабатывает снова — ждать сброса и возобновлять тех же сабагентов, новых не запускать.
- **Инцидент 3 (живые прогоны)**: ядро, запущенное с `--provider chatgpt` (переопределение), записало
  chatgpt основным провайдером в `~/.kvist/config.json` владельца (был OpenCode Go):
  `user_config.ensure_provider_credentials` (стр. ~741) для chatgpt безусловно вызывает
  `set_provider_secret(LLM_PROVIDER_KEY, "chatgpt")`. Ошибка была до этой работы; конфиг ведущий не читал и
  не правил (hard rule) — владельцу сообщено, как вернуть (`/provider` → OpenCode Go). По согласию владельца —
  задача **T15** (haiku, effort medium): круг 1 — сабагент правильно остановился: `/provider`, мастер и
  `--provider` идут одним `build_agent(provider_override=…)` → `ensure_provider_credentials`, развести их можно
  только в `main.py` (сейчас его правит T9). План: после слияния T9 — `bo/t15` на свежий experimental,
  разрешить `main.py`: `ensure_provider_credentials(..., save_selection=False)`, `True` только у явного выбора;
  проверить и второй писатель `kernel/ssh/credentials.py:89`. **Закрыто**: T15 слит `e7d6f30` (§6).
- Инцидент 2: лимит аккаунта сработал во время волны 2 (T6, T7 остановлены посреди работы);
  после сброса возобновлены с места остановки.

## 2. Сверка known_state с кодом

Все коммиты на месте: 4fafa12, 622b1a0, da98f46, cc7fffa, c1edd9f, 700224b, d345fe9,
edad255, d45484c, c3c1626, 8ee2fd4, 4325243, 326c4e1.

Долги — подтверждены:
- thinking снимается на шагах с tool_use: `kernel/llm/llm_clients.py` (`_replays_tool_use` →
  `del request_kwargs["thinking"]`). Подпись (`signature_delta`) и `redacted_thinking` вообще
  не читаются ни в потоке (`_normalize_stream_chunk`), ни без него (`_normalize_response`).
  **Закрыто T11** (`6694938`): `replays_tool_use_without_thinking` снимает thinking только для истории,
  где у хода с tool_use блоков нет.
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
- **D-24 Факты API anthropic для T10/T11** (источник: справочник claude-api Anthropic в этой сессии;
  поля сверены с типами установленного SDK `anthropic` 1.8.0, где есть `ThinkingBlock{thinking,signature}`,
  `RedactedThinkingBlock{data}`, `SignatureDelta`, `prefix_mismatch_behavior`):
  - кэш: ≤ 4 меток на запрос; порядок префикса tools → system → messages; минимальный префикс 512 ток.
    (Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.x), 1024 (Opus 4.8, Sonnet 5/4.6/4.5), 2048 (Opus 4.7),
    4096 (Opus 4.6/4.5, Haiku 4.5) — короче молча не кэшируется; каждая метка ищет прошлую запись
    не дальше 20 позиций назад (серия подряд `tool_use` или `tool_result` — одна позиция); запись
    читаема только после начала стрима первого ответа (отсюда сдвиг старта исполнителей); TTL 5 мин
    по умолчанию, `"1h"` по выбору; смена tools сбрасывает всё, смена system — system и messages;
    перестановка/добавление/удаление меток `cache_control` ничего не ломает;
  - thinking на Opus 5.5 / Sonnet 5.5 / Fable 5.1+: всегда включён (`disabled` и `budget_tokens` → 400 на
    Opus 5.5; у Sonnet 5.5 выключение — `between_tools`); принудительный `tool_choice` `any`/`tool` → 400;
    по умолчанию `display: "omitted"` (текст пуст, подпись есть); блоки отправлять **неизменными** —
    изменённый текст/подпись → всегда 400; подпись связывает префикс (system, набор tools, все
    сообщения до блока): правка ранних сообщений или смена набора tools делает недействительными все
    последующие блоки (400 на «enforced»-аккаунтах — у Opus 5.5 это аккаунты от 2026-08-31; у
    остальных записывается); допустимо: дописывать в конец, убрать ведущую серию блоков (с самых
    старых), хвостовую серию или все; смена модели — API сам выбрасывает чужие блоки (без оплаты),
    но по ТЗ (d1) мы их и не шлём; бета `thinking-binding-controls-2026-08-01` +
    `thinking.block_binding.prefix_mismatch_behavior: "error"|"drop_block"`.
  - следствия для Kvist: `compact_history` (урезание старых выводов) и дописывание tools скилла посреди
    прогона — это правки префикса → перед такой правкой хранимые thinking-блоки выбрасываются все;
    `redact_value` перед отправкой не должен трогать блоки.
- **D-26 Цепочка thinking (d1) в устройстве Kvist.** Журнал событий (`session_events`) — только для показа и
  в промпт не попадает; каждый ход начинает новый нативный разговор (прошлые ходы — текстовой сводкой в
  runtime-промпте), так что блоки прошлых ходов отправлять некуда. Значимые места цепочки: (1) внутри прогона —
  NativeTurn → `native_messages` → каждый следующий запрос (здесь и снимается долг «thinking снимается на шагах
  с tool_use»); (2) **после прерывания** — нативный разговор прогона с блоками пишется в сессию дописываемым
  файлом рядом с журналом (`.kvist/sessions/<name>.native.jsonl`, только новые сообщения, без перезаписи) и
  при `/resume` того же прогона на тех же провайдере и модели восстанавливается и отправляется дальше; на
  другой модели/провайдере — без блоков (D-17). Rust: `delete`/`reset` убирают этот файл, как журнал
  (паритет). На диск — в редактированном виде с дайджестом; блок с несошедшимся дайджестом выбрасывается.
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

- **D-27 Служебные файлы не входят в работу исполнителя** (баг из живого прогона: diff `+5 -24` в 3 файлах
  при нетронутых `calc.py`/`test_calc.py` — исполнитель переписал KVIST.md пользователя). Откуда что попадало:
  снимок S (`_make_snapshot`: `git add -A`, исключён только `.kvist`) берёт KVIST.md и KVIST-CHAT.md пользователя,
  если они не в его `.gitignore` (нет `.gitignore` — Kvist его не правит, `_add_instructions_to_gitignore`; с
  `.gitignore` они игнорируются и в worktree не попадают); затем `record_result` делал `git add -A` с тем же
  единственным исключением, так что правка блокнота и всё, что оставил запуск тестов (`__pycache__/*.pyc` проекта
  без своих правил; `.pytest_cache` сам себя игнорирует через внутренний `.gitignore`), шли в W, в `summary`/
  `full_diff` и в accept. Решение: (1) запись в KVIST.md / KVIST-CHAT.md корня рабочей папки из песочницы
  исполнителя (`SafeExecutor(protect_instruction_files=True)`; write/append/replace/delete/move) — отказ с
  объяснением; чтение не запрещено; (2) `record_result` не стейджит эти два пути (`:(exclude,literal)` с
  `workspace_rel`), `**/__pycache__/**` и `**/.pytest_cache/**`: исключённое остаётся таким, каким было в S, так
  что diff S..W, сводка судьи и accept его не видят (правка через shell тоже не доходит до результата).
  Не исключаем: `.gitignore` (файл пользователя, исполнитель вправе его править; строки, которые Kvist дописал
  сама, уже в S), прочие `*.pyc` вне `__pycache__`.

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
| T1 | a2 | менеджер worktree | `kernel/bestof/__init__.py`, `kernel/bestof/worktrees.py`, `kernel/tests/test_bestof_worktrees.py`, `pyproject.toml` (одна строка) | — | merged `a490abf` (2 круга) |
| T2 | a3 | события и протокол | `kernel/protocol/frontend_protocol.py`, `kernel/protocol/event_stream_ui.py`, `kernel/protocol/ui.py`, `kernel/session/session_events.py`, `docs/UI_API.md`, `docs/UI_API.ru.md`, тесты этих модулей | — | merged (2 круга) |
| T3 | a1 | сессии исполнителей, итоги за сессию | `kernel/session/agent_logging.py`, `kernel/session/session_store.py`, `kernel/session/session_usage.py` (новый), `kernel/agent/agent.py` (только конструктор), `kernel/agent/agent_model.py` (только `_emit_usage`), `kernel/session-rs/src/*`, тесты сессий | — | merged (2 круга) |
| T4 | e1 | glass: арена, лог, вердикт, council, статус | `frontends/tui/glass/**` | контракт §4 | merged (2 круга) |
| T5 | a4 | префикс кэша, стиль в конце, постоянные tools | `kernel/agent/agent_model.py` (промпт), `kernel/plan/plan_mode.py`, `kernel/agent/agent_runtime.py`, `kernel/providers/chatgpt_client.py`, `kernel/llm/prompt_prefix.py` (новый), тесты | T3 | merged `a564151` (2 круга) |
| T6 | b | исполнители (запуск со сдвигом, статус, выбывание) | `kernel/bestof/executors.py`, `kernel/bestof/styles.py`, тесты | T1 T2 T3 | merged (2 круга) |
| T7 | c | council + проводка команд-ходов | `kernel/bestof/council.py`, `kernel/prompts/bestof/*`, `kernel/protocol/event_stdio.py`, `kernel/protocol/slash_commands.py`, `kernel/protocol/core_manifest.py`, тесты | T2 T3 | merged (2 круга) |
| T8 | b | судья, прогон best-of, вердикт, принятие, `/best-of` | `kernel/bestof/run.py`, `kernel/bestof/judge.py`, `kernel/prompts/system/judge_prompt.txt`, `kernel/protocol/protocol.py`, `kernel/protocol/parser.py`, `kernel/agent/native_tools.py`, `kernel/agent/agent_runtime.py`, `event_stdio.py`, `slash_commands.py`, тесты | T5 T6 T7 | merged (3 круга) |
| T9 | b | предложение режима основным агентом | `protocol.py`, `parser.py`, `native_tools.py`, `agent_runtime.py`, ролевые промпты, `kernel/bestof/offer.py`, тесты | T8 | merged (2 круга) |
| T10 | d2 | аудит кэша anthropic, текстовый режим | `kernel/llm/llm_clients.py`, `kernel/agent/agent_model.py`, `kernel/agent/agent_context.py`, тесты | T5 | merged (1 круг) |
| T11 | d1 | thinking с подписью по всей цепочке | `kernel/llm/llm_clients.py`, `kernel/llm/anthropic_wire.py`, `kernel/agent/native_tools.py`, `kernel/agent/agent_model.py`, `kernel/session/agent_logging.py`, `kernel/session-rs` (если нужно), тесты | T9 T10 | merged `6694938` (1 круг) |
| T12 | e2 | pixel gui: те же экраны | `frontends/gui/tauri/ui/**`, `frontends/gui/tauri/tests/**`, README | T4 T2 | merged `7a573a5` (2 круга) |
| T13 | e3 | остальные фронтенды не ломаются, текстовый итог | по итогам аудита | T7 T8 | merged (1 круг) |
| T14 | — | живая проверка (сам) | — | T5 T7 T8 T10 | done (2 прогона, §9) |
| T15 | — | `--provider`/env/пересборки не сохраняют основной провайдер (инцидент 3) | `kernel/config/user_config.py`, `kernel/main.py`, `kernel/protocol/event_stdio.py`, тесты | T9 | merged `e7d6f30` (2 круга) |

Слитые коммиты: T2 `4e8ec9c`, T4 `7b2a63c`, T1 `a490abf`, T3 `21f6210`, T12 `7a573a5`, T5 `a564151`,
T6 `5986b86`, T7 `2ea0a5d`, T10 `de412ba`, T13 `8d44eda`, T8 `846f6a3`, T9 `6cf27d1`, T15 `e7d6f30`,
T11 `6694938`.

Волны: 1 — T1 T2 T3 T4; 2 — T5 T6 T7 (+T12, как только слит T4); 3 — T8; 4 — T9 T10 T13;
5 — T11; затем T14. Одновременно не больше 4 сабагентов; задачи с общим файлом идут друг за
другом. Пересмотр: T8 регистрирует роль `judge` в `agent_model._ROLE_PROMPT_FILES`, поэтому T8 и
T10 (оба `agent_model.py`) не идут вместе — T10 уходит в волну 4 (с T9 файлы не пересекаются).

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

### T2 — события и протокол
**Круг 1** (коммит `d98a441`, 7 файлов, +851/−5; 65 новых тестов; у сабагента полный набор
5130 passed / 75 skipped — крейт в его worktree не собран, поэтому `test_session_native`
пропущен; в `int` крейт собран).
- Обман: нет. Удалённые строки — два переписанных абзаца документации и строка `_write`.
- Процесс: одна правка (импорты в `event_stream_ui.py`) сделана скриптом через Bash, а не Edit;
  сабагент сообщил сам, содержимое проверено по diff. Записано как нарушение hard rule.
- Слабые места:
  1. `session_events.py` `_compacted`: `journal_notice` после компактации без штампа → **принято**,
     доработка.
  2. `event_stream_ui.py` `_usage_of`: `int()/float()` без защиты → **отклонено**: вход от
     оркестратора, не от модели; ошибка программиста должна падать громко.
  3. `event_stream_ui.py` `_PATCH_CHARS`: связь кода и документации → **отклонено**: так же
     задокументирован кап 4000 у `action_result` (§7).
  4. `session_events.py`: журналируемые события вне хода не пишутся → **принято как ограничение**:
     T8 шлёт их только внутри хода (в бриф T8).
  5. UI_API: значение `purpose` не названо → **принято**, доработка (`bestof_decision`).
  6. (ведущий) форма `rows[].tests` в контракте не была уточнена — моя недоработка; T2 выбрал объект
     `{state, summary}` как в `bestof_status`, это верно; T4 уведомлён.

**Круг 2** (коммит `b77d219`): `purpose: "bestof_decision"` в обеих документациях; функция
`_stamped`, `_compacted(raw, stamp)`, тест компактации со штампом (сабагент проверил, что тест падает
без передачи штампа). Принято. **Слито: `4e8ec9c`** (полный прогон в `int`: первый — 1 падение
известного флейка `test_app_screen`, повтор — 5155 passed / 51 skipped; ruff чисто).

**T1 слит: `a490abf`** (в `int`: 5211 passed / 51 skipped, ruff чисто).

### T4 — glass
**Круг 1** (коммит `23470b3`, 18 файлов, +3121/−33; cargo test 261 passed / 5 ignored — те же 5,
что на базе; clippy чисто).
- Обман: нет. Новых `#[ignore]` нет, тесты не удалены; `#[cfg(test)]` на `code.rs:counts` законен
  (функция нужна только тесту, clippy `--all-targets` собирает и бинарь). Правки clippy в старых
  файлах glass (на базе clippy 1.93 не проходил: 9 ошибок) — механические, приняты.
- Процесс: одна правка через `sed -i`, одна откатана и переделана через Edit (сообщил сам).
- Качество: разбор событий терпимый к отсутствующим полям; диалог решения отвечает ровно
  значениями D-13; лог исполнителя переиспользует штатный разбор журнала; события чужого прогона
  игнорируются.
- Слабые места:
  1. (ведущий) `arena.rs:93` при неизвестном `cache_read` весь вход считается некэшированным —
     выдумка → **доработка**: «≈» при частично неизвестном, не показывать при полностью неизвестном.
  2. (ведущий) `strings.rs` углы: speed смешан со скоростью работы программы, maintenance назван
     «Сопровождение» → **доработка** по ТЗ: «Скорость разработки», «Цена поддержки».
  3. `app.rs:1315` стрелки у арены → **отклонено**: во время хода ввод закрыт, Esc возвращает разговор.
  4. арена вместо разговора → **отклонено**: это и есть вид хода best-of.
  5. узкие окна `< 55` колонок → **отклонено**: glass требует kitty нормального размера.
  6. лог снимком + Ctrl+R → **отклонено**: события исполнителя по замыслу не в основном потоке.
  7. `bestof_diff` без повторного вопроса → **отклонено**: ядро по D-13 всегда спрашивает снова.
  8. `bestof_finished.message` двуязычие → **в бриф T8**: ядро шлёт на языке человека.
  9. не проверено в настоящем kitty → **в ручные проверки** и в живой прогон T14.
- Уточнение контракта (ведущий): `angle` в событиях council = id угла, локализует фронтенд.

### T12 — pixel gui
**Круг 1** (коммит `e207012`, 18 файлов, +1689/−1; 24 новых теста, node --test 57/57; ручная
проверка в браузере на mock-core через Playwright).
- Обман: нет; `tests/ui.test.mjs` не тронут, 33 старых теста зелёные; `src-tauri` не менялся.
- Точки подключения в существующих файлах минимальные (`actions.js` +8, `dialogs.js` +4, `main.js` +2,
  `reducer.js` +28, `index.html` +1) — приняты.
- Слабые места:
  1. (ведущий) `views/bestof.js:50` чипы tool mode/денег в существующей шапке разговора — меняют
     существующий экран в каждом прогоне (`tool_mode` есть всегда) → **доработка**: только в шапке слоя.
  2. `views/bestof.js:86` Esc закрывает слой только при фокусе внутри, иначе уходит в паузу хода →
     **доработка**: перехват на уровне документа, пока слой видим.
  3. `bestof.css:9` непрозрачный фон слоя → **доработка**: как у остальных оверлеев.
  4. многострочный `/best-of` уходит целью → **отклонено**: ядро принимает строку цели с префиксом (D-14).
  5. команды не в манифесте ядра → **отклонено**: добавят T7 (council) и T8 (best-of).
  6. `kind:"turn"` при повторном открытии → **отклонено**: только в журнале; состояние строится заново.
  7. `JSON.stringify` на кадр → **отклонено**: ≤ 6 строк.
  8. язык `bestof_finished.message` → **в бриф T8**.
  9. виды без DOM-тестов → **принято**: ручная проверка; окно Tauri — в ручные проверки.

**Круг 2** (коммит `e7f9c2f`): шапка разговора не трогается — полоса режима/денег внутри слоя
(`onScreen`, `barOf`, тест «обычный разговор ничего не добавляет»); Esc перехватывается на `document`
в фазе захвата, пока слой виден (диалоги, настройки и обзор сохраняют свой Esc) — паузы хода нет
(проверено вручную посреди прогона на mock); фон слоя по правилу `.feed-wrap` с `--glass`. node 58/58.
Осталось (принято): `:has()` в WebKitGTK окна Tauri не проверен — в ручные проверки.

### T9 — предложение best-of основным агентом
**Круг 1** (коммит `f9f5c8d`, 14 файлов, +1058/−9; 71 тест; полный набор 5700 passed). Действие `offer_best_of
{reason, count?}`, один раз за прогон, до первой правки; вопрос без внутренних терминов, «примерно в N+1 раз
дороже», «≈» сумма только из измеренных итогов сессии; согласие → в stdio-multi следующим ходом `/best-of N
<текст человека>`; отказ/закрытие — заметка модели. Флаг `bestof_follow_up` ставит только транспорт, который
умеет запустить следующий ход. Процесс: одна правка python-скриптом — нарушение hard rule (сообщил сам).
Доработка: подпись в `call_labels.py`, хук в цикле классики (`main.py`). Отклонено: «продолжай» как задача,
грубое среднее (подписано «≈»), preflight без кэша, флаг атрибутом, имя `_reject_plan_misuse`.
**Круг 2** (коммит `21306ef`; 5705 passed): подпись + тест; `run_follow_up_turn` в классике, хук в `main.py` после
успешного хода + 4 теста; UI_API — где предложение есть. Остаток: сам вызов хука в цикле `main.py` без
PTY-теста — в ручные проверки. Принято.

### T13 — остальные фронтенды
**Круг 1** (коммит `52763a1`, 43 файла, +1403/−35; Ink 505/505 + typecheck, VS Code 98/98 + typecheck, OpenCode
69 pass, Desktop 103/103, Zed 120, web-local 162, классика 12; полный pytest 5598 passed).
- Обман: нет; только новые тесты, фикстура fake-core дополнена сценариями.
- Аудит: новые события не роняют ни один фронтенд; команды-ходы были сломаны в 7 из 9 («Unknown
  command», строка как цель, web-local зависал в «идёт ход» после отказа, Ink/VS Code не давали
  остановить ход-команду) — всё починено и покрыто тестами; web-local помечал live-события как
  журналируемые — исправлено; тело диалога решения показывается целиком (кроме voice, см. ниже).
- Вне `frontends/`: `kernel/main.py` +5 строк (импорт и вызов `run_turn_goal` в цикле ввода классики) —
  **принято**: бриф прямо разрешал «точку, где классика разбирает slash-команды», а она в `main.py`
  (неясность брифа — ошибка ведущего). `turn-commands.ts` отдельно от `COMMANDS` (иначе пришлось бы
  трогать справочник для модели в `slash_commands.py`) — **принято как есть**.
- Процесс: одна правка своего нового теста через `sed -i` — нарушение hard rule (сообщил сам); проверка
  чужого флейка через `git stash` (общий стек!) — ведущий проверил: стек пуст, основной checkout чист.
- Слабые места: 1 (форма `body` угадана до T8) → **проверить после слияния T8** (живой прогон/фикстура T8);
  3 (voice режет `body` до 5 строк в окне, вслух читает целиком) → **принято** (голосовой интерфейс);
  4 (Zed без форм не ответит на 4 варианта → pending, решить можно командой) → **принято**;
  5–7 (DOM Desktop, Ctrl+C в PTY классики, хосты VS Code/JetBrains без запуска) → **в ручные проверки**.
- Найдено до этой работы: `frontends/web-local/tests/client/blocks.test.mjs:204` падает и без правок —
  в финальный отчёт (не в pytest).

### T8 — прогон best-of, проверяющий, решение
**Круг 1** (коммит `a4e768b`, 17 файлов, +2856/−26; ~130 новых тестов; полный набор у сабагента 5582 passed +
1 падение `hello.commands`, исправлено одной строкой; ruff чисто).
- Обман: нет (совпадения поиска — текст промпта про xfail и тест на формулировки промпта). Правка
  `test_event_stdio_commands.py` вне списка — законна (новая команда в `hello.commands`).
- Качество: команда-ход через `turns.py` и `turn_refusal` (отказ до открытия хода); проверяющий — основной
  агент вложенным `_run_turn(goal, "judge", woken=True)` с `_QuietReview` (человек видит вызовы, но не второй
  `final` и не имя роли), ход проверяющего убирается из чекпоинта и истории; `_READ_ONLY_ROLES = {reader,
  judge}`; `verdict` закрывает цикл; строгая проверка вердикта (строки на всех, один победитель, закончивший,
  с diff); стиль/тесты/diff в событии — из пула; задача и вердикт в `.kvist/bestof/<run>/decision.json`;
  ролевой промпт — принципы с причинами, порядок обман → тесты → качество, «не выдумывать победителя».
- Слабые места: 4 (пустые подписи шагов `executor`/`verdict`) → **доработка**, разрешён `call_labels.py`;
  5 (Stop во время диалога решения ничего не делает) → **доработка**: отмена = отложить (`pending`);
  7 (задача со слова `status/diff/accept/discard` читается как управление) → **доработка**: строка в UI_API;
  8 (тест отмены на sleep) → **доработка, если возможно**; 1 (приватный `_session_name`), 2 (вложенный
  `_run_turn`), 3 (промпт без живой модели — проверю в T14), 6 (`git diff` на `/best-of status`) → **отклонено**.

**Круг 2** (коммит `bca8afa`; полный набор 5598 passed / 80 skipped; ruff чисто). Подписи шагов `executor`/`verdict`
на обоих языках без «judge/executor/судь/исполнител» (тест); вопрос решения задаётся в своём потоке, ход
проверяет `control.interrupted()`, Stop → `postpone()` с `stopped:true` и `pending`; `take()` проверяет Stop до
`accept`; `control.begin()` перед `decide`; тесты через настоящий `request_cancel`; тесты без sleep (ожидание
событий; найдена и исправлена гонка открытия запроса до `cancel_all`); строка в UI_API про слова-команды.
Остаток: прерванный диалог остаётся на экране → **круг 3**: закрывать запрос `input_closed` с `reason:"cancelled"`
(разрешён `event_session.py`), UI_API.

**Круг 3** (коммит `d0aea77`; полный набор 5604 passed / 80 skipped; ruff чисто; модули best-of 3 раза подряд
зелёные с ошибкой на необработанное исключение в потоке). `FrontendChannel.close_requests(reason, purpose=…)`
шлёт `input_closed` и будит ждущего; `respond()` больше не принимает второй ответ на отвеченный/закрытый
запрос (совпадает с документированным «поздний ответ игнорируется»); `EventSession.cancel()/pause()`
закрывают только диалог `bestof_decision` и только если остановка дошла до хода; `reason: "cancelled"` в
UI_API (EN/RU). Тесты на настоящем stdio-цикле: один `input_closed` с тем же id до `final`, поток диалога
завершён, поздний ответ без ошибки. Остаток (принято): закрытие по `purpose`, а не по ходу — двух
одновременных best-of в сессии не бывает. **T8 принят после 3 кругов.**

### T10 — кэш anthropic, system текстового режима
**Круг 1** (коммит `7a9167e`, 4 файла: `llm_clients.py` +39/−10, `agent_model.py` +19/−1, `agent_state.py` +4,
новый `test_anthropic_cache.py` на 63 теста; полный набор 5313 passed / 80 skipped; ruff чисто).
- Обман: нет; существующие тесты не менялись. Процесс: опечатку в импортах своего нового теста
  поправил через `sed` — нарушение hard rule (сообщил сам), содержимое проверено тестами.
- Сделано: system прямого запроса — два блока по `### SELECTED STYLE` с меткой на каждом; меток
  никогда > 4 (32 комбинации); аудит 16 путей к клиенту (натив 3–4 метки, текст 2, чат 2–3, разовые
  вызовы 1, vision и doctor 0), шлюзы без меток; system текстового режима — один раз на прогон/роль/модель
  (`text_system_prompt`), байт в байт между шагами; сабагент проверил, что 4 теста падают без правки.
- **D-25** (принято): правки KVIST.md и стилей посреди прогона — в system со следующего прогона; секция
  плана — по старту прогона.
- Пункт 4 (метка на статической части user-сообщения) **не делан с замером**: свежий прогон — 326 ток. из
  738 (< порога 512), с историей — 1567 из 1979; выигрыш ≈1,3–7 % входа шага при правке строкового
  user-сообщения во всех провайдерах и обрезке. Принято.
- Слабые места: 1 (text → native → text берёт system старта прогона) → **отклонено**: это и есть
  D-25 и лучше для кэша; 2 (поток не проверен отдельно; `\n\n` в конце первого блока) → **в ручные
  проверки**; 3 (метка на коротких разовых вызовах) → **отклонено**: короче порога молча не кэшируется,
  поведение было и до задачи; 4 (чат пересобирает system на каждом сообщении, второй вызов хода — другой
  system) → **остаётся долгом** (вне задачи, `agent.py`) — в финальный отчёт.

### T7 — council и команды-ходы
**Круг 1** (коммит `9ebdf36`, 9 файлов, +1729/−16; 39 + 23 новых теста; полный набор зелёный).
- Обман: нет; единственная правка существующего теста — `hello.commands` с новой командой.
- Качество: `turns.py` (команда-ход: события, `turn_end`, `command_result`, журнал как у хода),
  строгая проверка выбора (`chosen` — участник с карточкой, `dissent` — только другие), текст решения
  копируется из карточки, промпты на принципах; `council` в `STDIO_ONLY_COMMANDS` (классика — T13).
- Слабые места:
  1. (ведущий) `council.py:169` сигнал «первый ответ» на возврате `create()` — у chatgpt и anthropic
     потоковый ответ ленивый генератор, запрос уходит на первой итерации → сдвиг не греет кэш →
     **доработка**: событие на первом полученном куске + тест.
  2. (ведущий) промпты константами в коде (из-за `test_pyproject_modules`) → **доработка**: в
     `kernel/prompts/bestof/*.txt`, разрешена правка `pyproject.toml` под новый каталог.
  3. (ведущий) заголовок «Совет» → **доработка**: имя режима «council» на обоих языках (ТЗ).
  4. `turns.py:69-104` копия кухни `LocalAgent.run` → **отклонено**: журнал и история под тестами; общий
     метод потребовал бы правки ядра хода для всех прогонов.
  5. остальные (приватный `_HelperUI`, гонка cancel, повтор как новый run, смысл reason, маркеры
     классификатора в тестах) → **отклонено**; фронтенды — T13.

**Круг 2** (коммит `6aa7f35`; полный набор 5277 passed / 80 skipped): событие «первый ответ» — на первом
куске потока (обёртка генератора), тест с ленивым потоком; промпты в `kernel/prompts/bestof/*.txt`,
`pyproject.toml` +2 строки (пакет и package-data); имя режима «council» в `final` и `command_result`.
`except BaseException` с немедленным `raise` в `_FirstAnswerClient` — принято: ничего не глушится, а
отмена тоже должна отпустить ждущих участников. Принято.

**T6 слит: `5986b86`** (в `int`: 5342 passed / 51 skipped, без предупреждений; ruff чисто).

### T6 — исполнители
**Круг 1** (коммит `7a02dc0`, 4 новых файла; 48 тестов модуля; полный набор зелёный).
- Обман: нет. Вне разрешённых файлов ничего нет.
- Блокер, найденный сабагентом: `terminal_validation._check_resolved_sensitive_path` ищет `.kvist` в
  **абсолютном** пути → любой `run_shell` в worktree (`.kvist/bestof/...`) отклоняется (моё допущение в
  разведке, что проверка относительная, было верно только для файловых действий — ошибка ведущего).
  Сабагент правильно остановился. **Решение ведущего**: разрешена правка `terminal_validation.py` и
  параметра конструктора `SafeExecutor` — явный флаг `internal_workspace`, который ставит только
  `ExecutorPool`; при флаге `.kvist` сверяется относительно workspace (свой `.kvist` worktree закрыт),
  без флага — как было. Обычные прогоны не меняются.
- Решения сабагента приняты: `finished` по `run_status == "completed"` (у помощников мёртвая попытка
  выглядела бы законченной), свой scratchpad, без `read_roots`, `routed_role="coder"`, заголовок
  сессии вручную, перезапись в worktree — `write_approval_enabled = False` на своём `SafeExecutor`.
- Слабые места: 1 → **доработка** (флаг + 5 тестов, включая настоящую песочницу); 8 (нет теста с
  настоящим `EventStreamUI`) → **доработка**; 2, 3, 4, 5, 6, 7 → **отклонено** (снимок diff по итогу
  достаточен; мусор проверки — редкий путь restart; гонка отмены ограничена 15 с; дублирование и
  приватный импорт допустимы; в подпапке git и так закрыт по D-23). Риск «moderate» для
  `python3/pytest` — оставить fail-closed: без bwrap авто-одобрение запускало бы код вне worktree.

**Круг 2** (коммит `182073f`; полный набор 5277 passed / 80 skipped, 1 warning; песочница и модуль 625 passed).
Флаг `workspace_inside_kvist` (по умолчанию False) в `SafeExecutor` → `TerminalExecutor` →
`_check_resolved_sensitive_path`: при флаге `.kvist` сверяется ниже workspace, без флага — как было;
проверка секретов не менялась. Побочная находка сабагента: `_sanitize_internal_paths_in_text` молча
выбрасывала строки вывода с абсолютным путём worktree (трейсбеки pytest) — при флаге путь workspace
вырезается перед проверкой. `terminal.py` (3 строки проводки флага) вне списка — **подтверждено**
ведущим. Тесты: обычный workspace внутри `.kvist` по-прежнему отказ; с флагом `cat`/`pwd` работают;
отказ для `.kvist` worktree (относительно, абсолютно, симлинк, cwd) и внешнего `.kvist`; настоящая
песочница — `check` passed/failed; git закрыт именно по D-23; настоящий `EventStreamUI` в
`JournalingUI`. Процесс: сабагент по ошибке сделал `git stash` и сразу `pop` (общий стек с основным
checkout) — ведущий проверил: стек пуст, основной checkout чист. Осталось: интеграционные тесты
идут в режиме `workspace` (без bwrap) — **в ручные проверки** (bwrap).

### T5 — префикс кэша, стиль в конце, постоянные tools
**Круг 1** (коммит `315297f`, 10 файлов, +491/−41; полный набор 5247 passed / 80 skipped, ruff чисто).
- Обман: нет. `test_tools_follow_the_phase` заменён на проверку постоянного набора — старый тест
  закреплял скрытие по фазе, что противоречит D-08; assert в `test_tools_leave_out_the_offer_…` расширен.
- Процесс: **две правки через python-heredoc** (`agent_model.py`, `plan_mode.py`) — нарушение hard rule,
  сабагент сообщил сам; третью заблокировала автоматика. Содержимое обеих проверено по diff построчно.
- Качество: порядок секций по D-05 (vision → KVIST.md из корня сессий → `## RESPONSE STYLE` + common →
  маркер + стиль → voice); `split_style_tail` берёт последний маркер в начале строки; набор tools
  фиксируется на первом нативном шаге, скиллы/MCP дописываются в конец; ключ chatgpt от общей части.
- Слабые места:
  1. маркер `## SELECTED STYLE` второго уровня выводит стиль из секции, на которую ссылается защита
     от джейлбрейка (`coder_prompt.txt:7` и др.) → **доработка**: `### SELECTED STYLE` + тест.
  2. отключённый посреди прогона скилл остаётся в списке → **отклонено**: ошибка резолвера, редко.
  3. отказ `delegate_task` проверен по частям → **отклонено**: достаточно.
  4. пересборка промпта при откате native→text меняет секцию плана → **передано в T10**.
  5. voice в хвосте → **отклонено**: у исполнителей один конфиг.
  6. стиль без файла → **отклонено**: T6 проверяет файлы стилей.
  7. живой кэш не измерен → **в T14**.

**Круг 2** (коммит `403d836`): `SELECTED_STYLE_MARKER = "### SELECTED STYLE"` (третий уровень —
стиль внутри `## RESPONSE STYLE`), тест «между `## RESPONSE STYLE` и стилем нет `\n## `» для coder,
reader, chat. Полный набор у сабагента 5250 passed / 80 skipped. Принято.

### T4 — glass, продолжение
**Круг 2** (коммит `d8d6cc4`): «некэш.» — точно, «≈» при частично неизвестном кэше, нет при
полностью неизвестном (4 теста); углы «Скорость разработки», «Цена поддержки»; тест формы `angle` = id.
Слияние: в `int` cargo test 265 passed / 5 ignored, clippy чисто, pytest 5155 passed / 51 skipped,
ruff чисто.

### T1 — менеджер worktree
**Круг 1** (коммит `806a63c`, 3 файла, +1482; 51 тест; полный набор 5117 passed / 51 skipped).
- Обман: нет; единственный `skipif` — платформенный (starttime из `/proc`), на Linux выполняется.
- Качество: алгоритм принятия ровно по D-09; сверх брифа — `diff-tree` для патча (без textconv),
  `--pathspec-from-file` + `GIT_LITERAL_PATHSPECS`, подпись `-S` при `commit.gpgsign`, мутационная
  проверка тестов. Тесты фиксируют HEAD, байты индекса и содержимое файлов.
- Слабые места:
  1. `worktrees.py:698` `_sync_files` после `update-ref` не атомарен → **принято**: правдивое
     состояние (accepted + хеш + команда досинхронизации в ошибке) и тест.
  2. `worktrees.py:491` `worktree add` запускает хуки пользователя → **принято**: служебные операции
     без хуков (`core.hooksPath` на пустую папку) и тест с post-checkout.
  3. `worktrees.py:315/345` pid без starttime вне Linux → **отклонено**: основная платформа Linux;
     ложное «жив» лишь откладывает восстановление, прогон можно выбросить.
  4. `worktrees.py:168` унаследованные `GIT_*` (pathspec, расположение) → **принято**: вычищать и тест
     с `GIT_LITERAL_PATHSPECS=1`.
  5. `worktrees.py:612` файл↔каталог, сабмодули, переименования → **частично**: тест переименования;
     остальное отклонено (редкость, отдельного поведения нет).
  6. `worktrees.py:350/468` блокировка только внутри процесса → **отклонено**: один core на сессию,
     run_id уникален, имена `.git/worktrees` git делает уникальными сам.
  7. `worktrees.py:690` успешная подпись не проверена → **отклонено**: нужен настоящий gpg; путь
     провала проверен.
  8. Открытый вопрос `.kvist/` в чужом `git status`/`git add -A` → **решение ведущего**: файл
     `.kvist/bestof/.gitignore` с `*` при первом прогоне (меняется только папка Kvist) и тест.
  9. (ведущий) `run.json` остаётся после accept → оставить: повторный accept из устаревшего диалога
     получает «уже принят».

**Круг 2** (коммиты `973ea65`, `7e93713`; 56 тестов модуля; полный набор 5121 passed / 75 skipped).
Сделано: `accept` записывает `accepted` сразу после `update-ref`, при сбое `_sync_files` —
`GitError` с хешами C и W и двумя командами `git restore` (worktree исполнителя оставлен);
`core.hooksPath` на пустую временную папку у каждого служебного вызова; вычищены 8 переменных
`GIT_*`; `.kvist/bestof/.gitignore` с `*`; тест переименования. Тесты проверены мутациями.
Новое красное было вне файлов задачи: `test_pyproject_modules` требует `kernel.bestof` в
`pyproject.toml` — сабагент правильно остановился; ведущий разрешил ровно одну строку.
Осталось слабым (принято): при сбое синхронизации — ручные команды; `reference-transaction`-хук
пользователя при принятии не срабатывает (служебные вызовы без хуков — осознанно).

### T3 — сессии исполнителей, итоги за сессию
**Круг 1** (коммит `e67c0bd`, 13 файлов, +1066/−61; полный набор 5116 passed / 75 skipped;
`test_session_native.py` 46 passed на собранном в worktree крейте; cargo test 25 passed, clippy чисто).
- Обман: нет. В `sessions.rs` — два схлопнутых `if` ради clippy 1.93, поведение то же.
- Процесс: одна правка через Bash heredoc, откатана и внесена заново через Edit (сообщил сам).
- Качество: все пути хранилища через `session_root(self)`; накопитель с «неизвестное остаётся
  неизвестным»; порядок сложения зафиксирован для побитового паритета Python/Rust.
- Слабые места:
  1. `agent.py:373,846`, `plugins/services.py:274` вложения от `executor.workspace` →
     **отклонено сейчас**: исполнителям и участникам вложения не передаются (их цель пишет Kvist),
     хранилища совпадают во всех реальных случаях; ограничение записано.
  2. `agent.py:288` `is_helper` для любого `service_session` → **принято как задумано**: исполнителю
     не нужны менеджер помощников, планировщик, трей, автопамять; фильтр tools тот же (в бриф T6).
  3. `agent_logging.py:204,698` итоги на диске только при записи документа, гонка при смене
     сессии → **отклонено**: в памяти итог точен, документ пишется в конце каждого хода; смена сессии —
     между ходами.
  4. `usage.rs` i64 против bigint → **отклонено**: недостижимые значения.
  5. `agent_model.py:1261` `call_cost` всегда → **отклонено**: один поиск в словаре на вызов.
  6. (ведущий) штамп журнала дочерней сессии не подключён (код T2 ещё не слит) → **доработка**
     после слияния T2: конструктор передаёт `stamp={"executor_id": ...}`, тест.
  7. (ведущий) текст §8a для UI_API — внести самому T3 в доработке (после T2 файл свободен).

**Круг 2** (merge `6bef34c` свежего experimental без конфликтов, коммит `168de52`): конструктор
передаёт `stamp={"executor_id": …}` только при непустом строковом `executor_id` (исполнитель и
участник council; у помощника и обычной сессии штампа нет), тест на четыре случая; §8a про
дочерние сессии и `usage`/`usage_total` внесён в обе документации. Полный набор у сабагента:
5186 passed / 51 skipped + 1 известный флейк (отдельно прошёл); `test_session_native` 46 passed;
cargo test 25, clippy чисто. Принято.

### T15 — переопределённый провайдер не становится основным (haiku, effort medium)

**Круг 1** (`9823d19`): `ensure_provider_credentials(save_selection=False)`, `build_agent(persist_selection=True)`,
классические пересборки текущего провайдера — `False`. Слабое место ведущего: stdio-путь (`launch`,
`launch_multi`, `rebuild` в `event_stdio.py`) — именно тот, на котором случился инцидент, — не тронут
→ **доработка**, файл разрешён.
**Круг 2** (`71afc9d`): launch/launch_multi — `False`; `rebuild` по умолчанию `False`; `True` только у
`/provider`, `/del-provider`, `/full-reset` (выбор человека в мастере) и `--session-op set-provider`;
`AGENT_LLM_PROVIDER` без `--provider` не сохраняется. Тест `launch_multi` на настоящем `main.build_agent`.
- Обман: нет. Процесс: отчёт сабагент слал повторно в цикле — остановлен ведущим после сдачи.
- Слабые места (приняты): классический `/provider` и stdio `/provider` проверены флагом, не записью в конфиг;
  путь выбора, забывший `True`, молча не сохранит (безопасная сторона); случай «env + /provider» без теста.
- Слияние: experimental ушла вперёд коммитом владельца `487f22e` (тот же файл, другой участок) — rebase,
  полный набор 5744 passed / 51 skipped, ruff чисто.

### T11 — thinking с подписью (sonnet)

**Круг 1** (`34f0ec4`, 15 файлов, +1597/−54). У ведущего в int: полный набор 5794 passed / 51 skipped;
`test_session_native.py` 47 passed (не пропущен, крейт собран в int); cargo test 26, clippy чисто; ruff чисто.
- Обман: нет; мутации сабагента (редакция блоков, сброс при смене tools/урезании) роняют тесты.
- Процесс: одна строка импорта вставлена скриптом, не Edit (сообщил сам).
- Решения: ключ `anthropic_thinking: {provider, model, blocks}`; один фильтр `request_messages` — блоки мимо
  `redact_value` и только для `AnthropicCompatibleClient` той же пары; при `/resume` с другой парой разговор
  строится заново; незавершённым вызовам — честное «результат не записан, могло сработать, проверь».
- Слабые места (приняты, в финальный отчёт): (1) файл всего нативного разговора (до 8 МиБ, с
  редактированными tool-выводами) остаётся в `.kvist/sessions` до удаления сессии или нового прогона;
  (2) повтор с `auto` на принудительный `tool_choice` узнаётся по подстроке `tool_choice` в тексте 400 —
  настоящего текста отказа в репозитории нет; (3) `getattr(stream, "get_final_message")` молча даёт «без
  блоков» на фейках без метода; (4) редактор, сработавший на тексте мысли, роняет блоки этого и ранних ходов
  при `/resume`; (5) fsync файла ~3 раза за шаг, не замерено; (6) вживую не проверено (anthropic запрещён).
- Принято с первого круга.

## 7. Брифы

Брифы целиком — `<repo>/.claude/bestof-briefs/{common,<id>}.md` (D-22); сабагент получает
те же файлы. Сабагенту даются: `common.md` (hard rules целиком, конвенции, формат отчёта) и
бриф задачи (цель, разрешённые/запрещённые файлы, решения, тесты, критерии приёмки).

## 8. Следующий шаг

Все задачи слиты (список в §5), живая проверка сделана (§9). Осталось владельцу: ручные проверки §11,
решение о слиянии `experimental` → `main`. Долги — в финальном отчёте сессии: выбывание вживую не
наблюдалось; ошибка JSON-режима chatgpt показывается как `error` в council; `max_output` у claude 5 = null;
путь чата пересобирает системный промпт на каждое сообщение; `blocks.test.mjs:204` (web-local) красный до
этой работы; в Zed без форм диалог решения на 4 варианта не ответить; известный флейк `test_app_screen.py`.

## 9. Живые прогоны (лимит 10)

| # | что | результат |
|---|---|---|
| 1 | best-of 2 (chatgpt, gpt-6.1-sol, effort high), временный репозиторий: исправить `average()` + тест | `tool_mode native/probe_ok`; 2 исполнителя (senior-dev, minimalist), сдвиг старта сработал (e2 стартовал после первого ответа e1, 19.3 с); **кэш между исполнителями**: первый вызов e2 — `cache_read_tokens` 2048 из 22 962 (общий префикс от e1); дальше на каждом шаге ~80–90 % входа из кэша (e1: 169 728 из 196 397 за 8 шагов); проверяющий: `set_check` → у обоих `passed` (3 теста); вердикт — победитель e1, причина; диалог решения → `accept` → ровно +1 коммит `338c779` (только `calc.py`, `test_calc.py`), worktree и ветки удалены; правка `.gitignore`, которую Kvist делает сам при старте, осталась незакоммиченной (D-09). **Выбывания не было** — оба варианта честные и зелёные; выбывание проверено только тестами. 83 с. |
| 2 | council (тот же репозиторий): «ValueError или None на пустом списке?» | 4 участника, 4 карточки (29–33 с: сдвиг старта сработал), 4 возражения с адресатами (52–55 с), вердикт выбрал `maintenance`, `decision` — текст его карточки, `final` — Markdown с карточками и возражениями, `command_result ok`. Все четыре угла пришли к одному решению (вопрос простой) — «против» нет. **Находка**: вызов выбора просит JSON-режим, chatgpt его отвергает, откат на обычный вызов (`_create_json_completion`, было до этой работы) показывается человеку как событие `error` «Provider rejected JSON mode…» — в долг для финального отчёта. 79 с. |

## 10. Открытые вопросы

- **Флейк до этой работы**: `kernel/tests/test_app_screen.py::test_the_live_preview_is_full_size_sharp_fast_while_the_pointer_moves_and_quiet_when_nothing_changes`
  (Xvfb, тайминги) падает под нагрузкой: на базе `experimental` @ `7015411` без наших правок — 1 из 5
  запусков, в `int` с T2 — 1 из 3. Правило слияния: полный прогон повторяется; вливается только
  зелёный; стабильное падение — стоп-условие и вопрос владельцу. В финальный отчёт.

- Как именно каждый фронтенд обрабатывает команду, которая запускает ход (прецедент `resume`:
  Ink различает `resume`, glass ждёт `command_result`) — T13.
- Можно ли исполнителю запускать `git status` в своём worktree внутри bwrap (gitdir основного
  репозитория вне worktree) — проверяется в T6.

## 11. Ручные проверки на живом API (копится)

Интерфейсы (без модели, но в настоящих окнах):
- Classic (glass) в настоящем kitty: арена, выбор строки и лог исполнителя (Enter/клик, Esc, Ctrl+R),
  диалог решения (a/d/p/x и ф/в/з/ч, Tab к патчу), council, статусная строка с «≈».
- Pixel (окно Tauri, WebKitGTK): слой best-of/council поверх ленты при `--glass < 1` (правило с
  `:has()`), русский интерфейс, узкое окно, Esc посреди прогона не ставит паузу.
- Kvist Desktop (окно Electron): `/council` и `/best-of` из поля ввода, отказ показывается заметкой.
- Классика: Ctrl+C посреди council и best-of останавливает ход; диалог решения печатает вердикт.
- VS Code и JetBrains (настоящие хосты): `/council`, `/best-of`, диалог решения.
- Voice: диалог решения в окне показывает 5 строк `body` (вслух — целиком) — оценить, достаточно ли.
- Исполнители в настоящем bwrap (не режим `workspace` тестов): запись в worktree, тесты, отказ git.
