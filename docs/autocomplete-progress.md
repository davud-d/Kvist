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

## В работе

- [ ] JetBrains-агент работает в своём git worktree: проверка EP для
  inline-completion под платформу 2025.3, `KvistCompletionClient.kt`,
  provider, `plugin.xml`.

## Осталось

- [ ] Строгая проверка JetBrains-плагина (дифф, компиляция, соответствие
  контракту) и слияние его ветки в `claude/wonderful-shannon-egazos`.
