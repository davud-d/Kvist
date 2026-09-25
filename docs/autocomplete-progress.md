# Прогресс: inline-автодополнение

Обновляется после каждого законченного шага. Контекст — `docs/autocomplete-research.md`
(что берём у конкурентов) и `docs/autocomplete-plan.md` (архитектура и контракт).

## Сделано

- [x] Веб-исследование конкурентов (Supermaven/Cursor/Copilot/Codeium),
  минусы и prompt-injection CVE — `docs/autocomplete-research.md`.
- [x] Аудит обеих IDE-интеграций и Python core (архитектура спавна
  процессов, существующие прецеденты `commit_message.py`/
  `commitMessage.ts`/`KvistCommitMessageClient.kt`) — фактаж собран,
  зафиксирован в `docs/autocomplete-plan.md`.
- [x] Контракт `main.py --complete` зафиксирован (раздел 2 плана).

- [x] Core готов: `completion.py` (одноразовый вызов `fast_model`, без
  `LocalAgent`, без ActionType — модель может вернуть только текст),
  `import_resolver.py` (только project-модули, сторонние библиотеки и
  stdlib не читаются вообще), `prompts/system/complete_prompt.txt`
  (explicit untrusted-input секция), регистрация в
  `agent_model._ROLE_PROMPT_FILES`, `--complete` в `main.py` (в раннем
  early-exit блоке, до тяжёлых импортов), `pyproject.toml` (новые модули
  зарегистрированы под `py-modules`).
- [x] 40 новых тестов (`tests/test_import_resolver.py`,
  `tests/test_completion.py`) — все зелёные. Полный набор:
  2376 passed, 22 failed (все 22 падают и без моих изменений — окружение
  без доверенного bubblewrap/TTY, проверено через `git stash`), 57
  skipped. `ruff check` — чисто.

- [x] VS Code готов и проверен, слит в эту ветку (`c44ab10`):
  `src/completionClient.ts` (spawn/debounce/отмена/LRU-кэш,
  `child_process.spawn` с массивом argv — без shell, инъекция аргументов
  невозможна), `src/completionProvider.ts`
  (`InlineCompletionItemProvider`), provider зарегистрирован в
  `extension.ts`, `activationEvents` дополнен `onStartupFinished` (иначе
  расширение не активируется при открытии файла), настройки
  `kvist.inlineCompletion.{enabled,debounceMs}` в `package.json`.
  Я сам (не со слов агента) прогнал `npm install/build/typecheck/lint/test`
  в его ворктри — чисто, 42/42 теста зелёные. Прочитал весь код построчно:
  не нашёл багов, не трогал запрещённые файлы (core/docs), не роутит
  completion через долгоживущий core-процесс.

- [x] JetBrains готов и проверен, слит в эту ветку (merge commit после
  `a677a7c`): реальный extension point платформы 2025.3 —
  `com.intellij.codeInsight.inline.completion.DebouncedInlineCompletionProvider`
  (найден и подтверждён по декомпилированным `.class` из реально
  скачанного SDK, не по памяти) — debounce и отмена-при-новом-запросе
  берутся у платформы бесплатно, руками написан только мост
  coroutine-cancellation → `ProgressIndicator.cancel()` →
  `destroyProcess()` в `KvistCompletionClient.kt` (таймаут 4с, не 120с как
  у commit-message), LRU-кэш по SHA-256 хвоста префикса/головы суффикса,
  тумблер on/off через `PropertiesComponent`
  (`KvistCompletionSettings.kt` + `ToggleInlineCompletionAction.kt` в меню
  Tools), новая запись `<inline.completion.provider>` в `plugin.xml`.
  Я сам (не со слов агента) прогнал `./gradlew compileKotlin --rerun-tasks`
  (реальная компиляция, не из кэша) и `./gradlew build` — оба зелёные
  против настоящего SDK 2025.3. Прочитал весь код построчно: не нашёл
  багов, не трогал запрещённые файлы, не роутит completion через
  `KvistCoreProcess`, cancellation корректно ре-бросает
  `CancellationException` (не глушит).

## Осталось

- [ ] Ничего блокирующего не осталось. Оба плагина реализованы, проверены
  и слиты в `claude/wonderful-shannon-egazos`. Не проверено ни одним из
  агентов и мной: живой запуск в реальной IDE (нет Extension
  Host/`runIde` в этом окружении) — честно отмечено как неподтверждённое
  во всех отчётах, а не выдано за проверенное.
