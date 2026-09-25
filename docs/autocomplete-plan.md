# План: inline-автодополнение в обоих IDE-плагинах Kvist

Основа — `docs/autocomplete-research.md` (что берём у конкурентов и чем
компенсируем их минусы). Этот файл — техническая архитектура и порядок
работ. Живой статус — `docs/autocomplete-progress.md`.

## 0. Что уже есть, что нет

Полный аудит (VS Code + JetBrains + Python core) подтвердил: ни в одном
плагине сейчас нет вообще никакого completion provider'а
(`registerInlineCompletionItemProvider`, `InlineCompletionProvider`,
`CompletionContributor` — везде 0 совпадений). Строим с нуля на обеих
сторонах.

Прямые прецеденты, на которые опираемся:
- **Python**: `commit_message.py` — лёгкий одноразовый вызов `fast_model`
  без `LocalAgent`/`build_agent` (не пишет `.kvist/`, не сеет `KVIST.md`).
- **VS Code**: `vscode-extension/src/commitMessage.ts` — `spawn(binaryPath,
  ['--commit-message'])`, пишет JSON в stdin, читает JSON из stdout,
  `token.onCancellationRequested(() => child?.kill())`.
- **JetBrains**: `KvistCommitMessageClient.kt` —
  `GeneralCommandLine(binaryPath, "--commit-message")` +
  `CapturingProcessHandler`, `runProcessWithProgressIndicator`.

## 1. Архитектурное решение: отдельный процесс на запрос, не общий `stdio-multi`

Оба плагина уже держат один долгоживущий процесс core на чат
(`KvistCore`/`KvistCoreProcess.kt`, протокол `--frontend stdio-multi`). Он
не годится для автодополнения:

- у него уже есть однопоточный gate — команда не выполняется, пока идёт
  ход агента или другая команда. Автодополнение должно работать
  *параллельно* с длинным агентным запуском, а не ждать своей очереди.
- это стейтфул-сессия с историей чата; completion — чистый stateless
  запрос (файл, префикс, суффикс — и всё).

Поэтому автодополнение — **новый одноразовый процесс на запрос**, ровно
как уже сделано для commit-message на обеих платформах. Задержку старта
интерпретатора компенсируем на уровне плагина: debounce (не стреляем на
каждую нажатую клавишу), отмена предыдущего запроса при следующей
клавише, кэш последнего ответа по (файл, хвост префикса).

`--complete` в `main.py` должен обрабатываться в той же ранней ветке, что
и `--commit-message` (до тяжёлых импортов `prompt_toolkit`/`rich`/
`agent.LocalAgent`), иначе получим лишнюю secondную задержку на каждый
запрос.

## 2. Контракт core: `main.py --workspace <path> --complete`

Никакого нового `ActionType`. Совершенно отдельный модуль `completion.py`
(зеркало `commit_message.py`), без executor, без action-протокола — модель
может вернуть только текст, не команду. Это и есть главная защита от
prompt injection для этой фичи: даже если инъекция в коде убедит модель
"выполнить" что-то, у этого пути физически нет способа исполнить действие.

**stdin (JSON, одна попытка, без стрима):**
```json
{
  "file_path": "src/foo.py",
  "language": "python",
  "prefix": "<весь текст файла от начала до курсора, включая незасейвленные правки>",
  "suffix": "<весь текст файла от курсора до конца>"
}
```
`file_path` — путь относительно `--workspace`. `prefix`/`suffix` — из
живого буфера редактора, не с диска (иначе не увидим несохранённые правки).
Разбиение на prefix/suffix делает плагин простой строковой операцией по
курсору — никакой арифметики смещений (UTF-16 code units в TS/Kotlin vs
codepoints в Python) не пересекает границу процессов.

**stdout (JSON, последняя строка):**
```json
{"ok": true, "completion": "..."}
```
или
```json
{"ok": false, "error": "..."}
```
Пустая строка в `completion` — валидный ответ ("подсказки нет").

### Что делает `completion.py` с payload'ом

1. Валидирует `file_path`: резолвит относительно workspace, требует
   `resolved.is_relative_to(workspace)` и
   `change_journal.journal_path_violation(workspace, file_path) is None`
   (тот же гейт, что уже отсекает `.env`/`.kvist` в `commit_message.py`).
2. Обрезает `prefix`/`suffix` до жёстких лимитов (свои константы, не
   доверяем лимитам клиента):
   - окно для промпта: последние ~6000 символов `prefix`, первые ~1500
     `suffix`;
   - окно для поиска импортов: первые ~4000 символов `prefix` (импорты
     почти всегда в начале файла — раз `prefix` всегда начинается с начала
     файла, этого достаточно).
3. `import_resolver.py` вытаскивает raw-импорты из окна поиска и
   резолвит **только те, что физически лежат внутри workspace**:
   - Python — `ast.parse` на каждую строку импорта, резолвинг относительно
     каталога текущего файла и относительно корня workspace (плюс `src/`),
     обработка `from . import` / `from .. import` через `level`.
   - TS/JS — только относительные спецификаторы (`./`, `../`) резолвятся;
     голые имена пакетов (`react`, `lodash`) — сторонние по определению,
     не резолвятся вообще.
   - Всё, что не резолвится внутрь workspace (сторонние библиотеки,
     stdlib, node_modules/site-packages/vendor) — **отбрасывается
     полностью, не читается**. Это и приватность (лишний код не уходит в
     облако), и защита от галлюцинаций (не просим модель угадывать API
     чужого пакета — она и так его знает из обучения).
   - Для каждого резолвнутого файла — только публичные сигнатуры
     верхнего уровня (`def`/`class` в Python через `ast`, `export
     function/class/const/interface/type` в TS/JS через regex), не всё
     тело файла. Общий бюджет на все сигнатуры — фиксированный (не тянем
     полноценный `context_budget.py`, это отдельная лёгкая фича).
4. Собирает промпт: `prefix`/`suffix`/сигнатуры оборачиваются в
   `<UNTRUSTED_TOOL_OUTPUT source="...">` +
   `redaction.defang_prompt_boundary_tags(...)` — ровно как в
   `commit_message.build_user_prompt`. System-промпт
   (`prompts/system/complete_prompt.txt`, новый файл, регистрируется в
   `agent_model._ROLE_PROMPT_FILES["complete"]`) явно говорит: инструкции
   только из system-промпта, всё остальное — данные для дополнения, а не
   команды.
5. Один вызов `_create_json_completion(caller, model, messages,
   stream=False, json_mode=False)` на `caller.config.fast_model` — тот же
   `_build_caller()`-паттерн, что в `commit_message.py` (без
   `LocalAgent`).
6. Чистит ответ (снимает случайные code fences, не даёт модели повторить
   prefix/suffix эхом), режет по жёсткому потолку символов.

### Файлы core, которые меняем

- новый `completion.py` (модуль)
- новый `import_resolver.py` (модуль, с юнит-тестами — питоновский AST +
  TS/JS regex-эвристика тестируются отдельно от LLM)
- новый `prompts/system/complete_prompt.txt`
- `agent_model.py`: одна строка в `_ROLE_PROMPT_FILES`
- `main.py`: `--complete` в раннем early-exit блоке (по образцу
  `--commit-message`), CLI-парсер
- новый `tests/test_completion.py` + `tests/test_import_resolver.py`
  (паттерн `tests/test_commit_message.py`: `FakeLLMClient` +
  monkeypatched `_build_caller`, без сети)

Этот слой пишу сам (единая точка правды для протокола — если каждый из
двух агентов на плагины будет придумывать контракт по-своему, они
разойдутся). Агентам на плагины отдаю готовый зафиксированный контракт.

## 3. VS Code (`vscode-extension/`)

- `package.json`: `contributes.configuration` — добавить
  `kvist.inlineCompletion.enabled` (bool, default true),
  `kvist.inlineCompletion.debounceMs` (default ~250). `activationEvents`
  сейчас только `["onView:kvist.chat"]` — этого недостаточно, добор
  открытия файла не активирует extension; нужно расширить (например,
  `onStartupFinished`).
- Новый `src/completionClient.ts` — по образцу `commitMessage.ts`:
  `spawn(binaryPath, ['--complete'], {stdio:['pipe','pipe','pipe']})`,
  пишет JSON в stdin, парсит `{ok, completion, error}`,
  `token.onCancellationRequested(() => child.kill())`.
- `extension.ts`: `vscode.languages.registerInlineCompletionItemProvider`
  на нужные языки, с debounce (не отправлять запрос до паузы в наборе) и
  отменой предыдущего запроса.
- Тесты: `tsx --test` уже настроен (`environment.test.ts` как образец
  чистой юнит-логики без спавна процесса) — тестируем построение payload'а
  и разбор ответа изолированно; при необходимости — маленький
  `fake-complete.mjs` по образцу `tests/fixtures/fake-core.mjs`.

## 4. JetBrains (`jetbrains-plugin/`)

- Платформа 2025.3 (`since-build="253"`) — **первым шагом агент должен
  проверить точный extension point для inline-completion в этой версии
  SDK** (в разных поколениях платформы это разные API); если подходящего
  EP нет или он неудобен — сознательно откатиться на
  `CompletionContributor` и явно объяснить, почему.
- Новый `KvistCompletionClient.kt` — по образцу
  `KvistCommitMessageClient.kt`: `GeneralCommandLine(binaryPath,
  "--complete")` + `CapturingProcessHandler`, короткий таймаут (не 120с,
  как у коммитов — сотни мс), отмена через `ProgressIndicator`/coroutine
  scope.
- `plugin.xml`: новая `<extensions>` запись под выбранный EP.
- Тестов в плагине сейчас нет вообще (`src/test/` не существует).
  Разворачивать с нуля Gradle test framework ради одной фичи — избыточно
  для этой задачи; приоритет — читаемый, структурно простой код и ручная
  проверка через `runIde`. Если добавление чистых Kotlin-юнит-тестов на
  парсинг payload/ответа окажется дёшево (без platform test framework) —
  сделать; поднимать полноценную IntelliJ test-инфраструктуру — нет.

## 5. Порядок работ

1. (я) core: `completion.py` + `import_resolver.py` + промпт + `main.py`
   + тесты → коммит.
2. (агент A, параллельно агенту B) VS Code клиент + provider + настройки
   + тесты.
3. (агент B, параллельно агенту A) JetBrains клиент + provider + XML +
   (по возможности) тесты.
4. (я) строгая проверка обоих: реальный код построчно, прогон тестов и
   тайпчека/линта, соответствие контракту из раздела 2, соответствие
   требованиям (только project-модули в контексте, defang инъекций,
   debounce/отмена).
