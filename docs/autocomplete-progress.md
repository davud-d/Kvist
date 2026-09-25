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

## В работе

- [ ] Два агента запущены параллельно, каждый в своём git worktree (не в
  этой ветке напрямую — сливаю их результат вручную после строгой
  проверки):
  - VS Code: `src/completionClient.ts`, provider в `extension.ts`,
    настройки в `package.json`, тесты.
  - JetBrains: проверка EP для inline-completion под платформу 2025.3,
    `KvistCompletionClient.kt`, provider, `plugin.xml`.

## Осталось

- [ ] Строгая проверка обоих плагинов (диффы, тесты/линт/тайпчек,
  соответствие контракту) и слияние их веток в `claude/wonderful-shannon-egazos`.
