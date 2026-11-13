# Бенчмарки

Kvist запускается на [Terminal-Bench](https://www.tbench.ai/) и других наборах задач
харнесса [Harbor](https://harborframework.com/) через `scripts/bench/kvist_harbor.py`.
Harbor поднимает каждую задачу в отдельном контейнере, ставит туда Kvist, передаёт
формулировку задачи, а потом запускает тесты задачи.

## Как Kvist работает внутри задачи

- **Код.** Адаптер упаковывает `kernel/`, два фронтенда, которые импортирует ядро
  (`frontends/tui/classic`, `frontends/zed`), `scripts/run_task.py` и
  `requirements.txt` из **коммита HEAD** (`git archive`), а не из рабочего дерева.
  Перед прогоном нужно закоммитить изменения; коммит записывается в результат как
  версия агента.
- **Python.** uv ставит в `/opt/kvist` собственный Python 3.13 и пакеты ядра. Python,
  пакеты и HOME задачи остаются такими, какими их собрала задача.
- **Песочница.** `run_task.py --container` включает `AGENT_SANDBOX_MODE=container`:
  песочницей служит контейнер задачи, поэтому команды идут без обёртки, с HOME задачи
  и сетью контейнера. Правила, которые защищают машину человека (`bash -c`, `rm`,
  запуск программ из workspace, лимит команды в 2000 символов), сняты, а на каждый
  вопрос ответ «да». Каждое «да» пишется в stderr, так что по логу видно, что было
  пропущено. Вне контейнера Docker или Podman и режим, и `--container` отклоняются.
  Секреты в окружение команд по-прежнему не попадают.
- **Workspace.** Рабочий каталог контейнера. Kvist не создаёт там `KVIST.md` и не
  трогает `.gitignore`; своё состояние (`.kvist/`) он переносит в логи агента до
  запуска тестов.
- **Логи.** `agent/kvist.txt` (весь прогон по шагам) и `agent/kvist-state/` (сессия,
  журнал изменений) в каталоге попытки. Входные и выходные токены суммируются из лога
  в результат попытки.

## Запуск

Нужны Docker (или Podman, `-e podman`) и Harbor (`uv tool install harbor`). Модель
задаётся как `<id провайдера Kvist>/<id модели>`; ключ берётся из окружения хоста под
именем, которое использует Kvist, — `<PROVIDER>_API_KEY` (`<PROVIDER>_BASE_URL`
меняет адрес API).

```bash
export INCEPTION_API_KEY=...
cd <каталог Kvist>
export PYTHONPATH=scripts/bench

# Сначала одна задача, чтобы увидеть всю цепочку
harbor run -d terminal-bench@2.0 -i '*fix-git' -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 1

# Небольшая выборка по одной попытке, чтобы замерить токены на задачу
harbor run -d terminal-bench@2.0 -l 10 -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 4

# Полный прогон для таблицы: все задачи, пять попыток
harbor run -d terminal-bench@2.0 -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 4 -k 5
```

`harbor jobs resume` продолжает прерванное задание. В Terminal-Bench 2.0 89 задач;
большинство просит 1 CPU и 2 ГБ памяти, самые тяжёлые — 4 CPU и 8 ГБ, каждая до 10 ГБ
диска. Образы задач остаются в Docker после прогона, так что при нехватке места их
нужно чистить.

## Заметки про Podman

Окружению Podman в Harbor нужен compose (подходит `podman-compose`). Чтобы оно не
падало, нужны две настройки: `PODMAN_COMPOSE_WARNING_LOGS=false` (иначе его баннер
читается как вывод команды) и `registries.conf` со строкой
`unqualified-search-registries = ["docker.io"]` (образы задач названы короткими
именами). Файл можно передать на один запуск через `CONTAINERS_REGISTRIES_CONF=<файл>`.
