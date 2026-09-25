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

## В работе

- [ ] Core: `completion.py` + `import_resolver.py` + `prompts/system/complete_prompt.txt`
  + регистрация в `agent_model._ROLE_PROMPT_FILES` + `--complete` в `main.py`
  + `tests/test_completion.py` + `tests/test_import_resolver.py`.

## Осталось

- [ ] VS Code: `src/completionClient.ts`, provider в `extension.ts`,
  настройки в `package.json`, тесты.
- [ ] JetBrains: проверить EP для inline-completion под платформу 2025.3,
  `KvistCompletionClient.kt`, provider, `plugin.xml`.
- [ ] Строгая проверка обоих плагинов (диффы, тесты/линт/тайпчек,
  соответствие контракту).
