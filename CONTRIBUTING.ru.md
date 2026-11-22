# Участие в разработке

[English version](CONTRIBUTING.md)

## Окружение разработки

Нужен Python 3.11 или новее.

```bash
git clone https://github.com/davud-d/Kvist
cd Kvist
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m kernel --workspace "<путь>"   # запуск агента
```

На Windows используйте `py -m venv .venv` и `.venv\Scripts\python.exe`. Для
фронтендов нужен Node.js (и Bun для OpenCode); у каждого свой README со сборкой.
Устройство репозитория описано в [`CLAUDE.md`](CLAUDE.md).

## Проверки

Запустите перед pull request:

```bash
.venv/bin/python -m pytest              # все наборы Python
.venv/bin/python -m pytest -m offline_smoke   # быстрые, без ключей
.venv/bin/python -m pytest -m slow      # медленные тесты с PTY и реальными процессами
.venv/bin/python -m ruff check .
```

Тесты не должны ходить в сеть и запускать настоящий tray-демон; используйте
заглушки из `kernel/tests/conftest.py`. Если вы меняете фронтенд, запустите и его
тесты, например `cd frontends/tui/ink && npm install && npm test`.

## Ветки

Любое изменение сначала попадает в ветку `experimental`, никогда прямо в `main`.
Открывай pull request в ветку `experimental` (base: `experimental`).
Pull request в `main` тоже примут и перенесут сами. `main` двигается только
fast-forward-слиянием из `experimental` после того, как мейнтейнер проверил
изменение.

## Соглашения

- Каждый модуль Python начинается с `# -*- coding: utf-8 -*-` и
  `from __future__ import annotations`.
- Строки для пользователя двуязычные, английский и русский.
- Изменение документа требует того же изменения в его русской паре (`*.ru.md`).
- Комментируйте «почему», а не «что». Без молчаливого `except`: ловите конкретные
  исключения.
- Сообщения коммитов в формате `type(scope): summary`, например
  `fix(agent): ...`, `feat(ui): ...`. Одно изменение — один коммит.

## Безопасность

Об уязвимостях не сообщайте в issue и pull request; см.
[`SECURITY.ru.md`](SECURITY.ru.md).
