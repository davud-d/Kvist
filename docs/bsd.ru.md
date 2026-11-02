# Kvist на FreeBSD, OpenBSD и NetBSD

**Экспериментально.** Ядро и классический терминал написаны так, чтобы работать там, но на настоящем BSD их пока
никто не запускал: единственная проверка — задача `bsd` в `.github/workflows/platforms.yml`, которая поднимает каждую
систему в QEMU и гоняет быстрые тесты без ключей. Первые запуски — это отчёт о том, что ещё ломается; присылай, что увидишь.

## Установка (из исходников)

Готового бинарника нет: PyInstaller на BSD не собирается, поэтому `install.sh` останавливается и отправляет сюда.
У пакетов с нативным кодом нет BSD-колёс, поэтому они берутся из пакетов самой системы (имена ниже — те, что
использует CI).

```sh
# FreeBSD 15
sudo pkg install -y python312 py312-pip py312-sqlite3 py312-pillow py312-cryptography py312-pydantic2 \
    py312-pydantic-core py312-tiktoken py312-jiter py312-keyring py312-paramiko git ripgrep
# OpenBSD 8
doas pkg_add python py3-pip py3-Pillow py3-cryptography py3-pydantic py3-pydantic-core py3-tiktoken py3-jiter \
    py3-keyring py3-paramiko git ripgrep
# NetBSD 11 (у tiktoken и jiter нет пакетов: их собирает pip, для этого нужен rust)
sudo pkgin -y install python312 py312-pip py312-Pillow py312-cryptography py312-pydantic py312-pydantic-core \
    py312-keyring py312-paramiko git-base ripgrep rust

python3 -m venv --system-site-packages .venv      # python3.12 / python3.14, где команда с версией
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m kernel --workspace /путь/к/проекту
```

`kvist --doctor` (или `/doctor`) покажет, чего не хватает.

## Чем отличается от Linux

- **Нет песочницы ОС.** Bubblewrap только для Linux, а в BSD нет cgroups. Поэтому в обычном режиме `auto` команда,
  которая запускает код (скрипт, `python -c`, `make`, запуск тестов), отклоняется. `AGENT_SANDBOX_MODE=workspace`
  разрешает её: это **не** изоляция, одобренная команда работает с твоими правами, и Kvist говорит об этом при старте.
  Чтение и правка файлов, `git`, `rg` и другие команды, не запускающие код, работают и без этого.
- **Сеть команде отключить нельзя.** Каждое подтверждение несёт об этом пометку.
- **Остановка команды** убивает её группу процессов, а ушедшего из группы (`setsid`) находит перечисление процессов через
  `ps` (ядро на Linux читает `/proc` и использует cgroup). Это best effort.
- **Каталоги пакетов.** `/usr/local/bin` (FreeBSD, OpenBSD) и `/usr/pkg/bin` (NetBSD) попадают в PATH дочерних процессов, если
  они принадлежат root и недоступны другим на запись, поэтому `git`, `python3` и `rg` находятся.
- **Недоступно:** скиллы, запускающие код (им нужен Bubblewrap), готовый установщик и бинарники, значок в трее,
  голосовой интерфейс. Терминалы Ink, Glass и OpenCode, десктопные приложения и плагины для IDE не пробовались.

## Потом

Настоящая песочница под каждую систему (`pledge`/`unveil` на OpenBSD, Capsicum или jail на FreeBSD) позволила бы режиму
`auto` запускать код и там. Это отдельная работа, связанная с безопасностью, и тестировать её можно только на самих системах.
