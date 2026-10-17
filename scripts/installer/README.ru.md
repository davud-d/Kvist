# scripts/installer/

Всё, что ставит Kvist на машину. Каждый скрипт работает в Linux и Windows
(и, по мере возможности, в macOS).

| Скрипт | Что делает |
| --- | --- |
| `install.py` | Главный вход: показывает установщики ниже списком со стрелками, все отмечены по умолчанию, и запускает оставленные вами. |
| `install_cli.py` | Ставит команду `kvist` и автономный бинарник (собирает его через PyInstaller, если в `dist/` его нет). Раньше лежал в корне репозитория, потом в `install.py`. |
| `install_jetbrains.py` | Находит все установленные IDE JetBrains и распаковывает плагин **Kvist Launcher** в папку `plugins/` каждой из них. |
| `install_vscode.py` | Находит команды запуска VS Code, VS Code Insiders и VSCodium и ставит `.vsix` Kvist в каждый редактор. |
| `install_gui.py` | Ставит Electron-GUI как настольное приложение: ярлыки в меню «Пуск» и на рабочем столе в Windows, иконки hicolor и `.desktop` в Linux, копия `Kvist.app` в `~/Applications` в macOS. |
| `install_zed.py` | Регистрирует установленный `kvist-core` как внешний ACP-агент Zed: одна запись `agent_servers` в файле настроек Zed, комментарии и всё остальное не трогаются. |
| `install_kvide.py` | Добавляет генератор сообщений коммита Kvist в редактор kvide: копирует адаптер (`frontends/kvide/`) рядом с `kvist-core` и пишет пользовательский `generators.toml` kvide, так что обычный `kvide` показывает кнопку. Чужой пользовательский файл не перезаписывается и не удаляется. |
| `jsonc_settings.py` | Точечный редактор одной записи JSONC-документа. Чистая работа с текстом; единственный вызывающий — `install_zed.py`. |
| `_common.py` | Общие помощники: тихий лог и спиннер, через которые отчитываются установщики, безопасная распаковка ZIP, поиск IDE JetBrains. |

У релиза есть собственные близнецы этих установщиков без зависимостей — `scripts/release/install.sh` и `install.ps1`: они скачивают ассеты релиза и ставят бинарник CLI, плагин JetBrains и расширение VS Code (без Zed и GUI). `install.sh` работает в Linux и, по мере возможности, в macOS (хватает системного bash 3.2); на Mac он выбирает `kvist-macos-arm64` или `kvist-macos-x64` по процессору. `plugin-release.yml` прикладывает их к скользящему релизу, CI проверяет оба на подставных ассетах. У `install.sh` есть и режим с экраном: в терминале он скачивает агент и закреплённую версию Node.js (с проверкой SHA-256) во временную папку и запускает `kernel --installer`, который открывает экран установки из Ink-бандла (`frontends/tui/ink/setup`, шаги `components` и `installing`), а затем первую настройку. Экран лишь управляет скриптом: `install.sh --plan` печатает по компонентам факты в JSON (пути, команды, доступность), `install.sh --only <id> --events` ставит и сообщает по одному JSON-объекту в строке, а `--cache <папка>` использует уже скачанные загрузчиком ассеты. `KVIST_INSTALL_PLAIN=1` пропускает экран. Компоненты: `cli`, `node`, `ffmpeg` (закреплённая статическая сборка для видео-превью; Linux x86_64 и macOS), `jetbrains`, `vscode`, `path` (строка PATH не добавляется, пока её не выбрали, и дописывается один раз) и под свёрнутой строкой инструменты сборки `python`, `libpython`, `binutils` (в экране отмечены с самого начала, у самого скрипта в умолчания не входят): системные пакеты, нужные `install_cli.py`; ставятся через apt-get, dnf, zypper или pacman от root или sudo без пароля (если нужен пароль, экран сначала запускает `sudo -v`; без sudo команды только печатаются).

```bash
python scripts/installer/install.py                      # выбрать стрелками
python scripts/installer/install.py --all                # всё, без вопросов
python scripts/installer/install.py --cli --gui          # названные компоненты, без вопросов

python scripts/installer/install_cli.py                  # или по одному напрямую
python scripts/installer/install_jetbrains.py            # --build — сначала собрать ZIP плагина
python scripts/installer/install_jetbrains.py --uninstall
python scripts/installer/install_vscode.py               # --build — сначала собрать VSIX
python scripts/installer/install_vscode.py --uninstall
python scripts/installer/install_gui.py                  # --build — сначала запустить electron-builder
python scripts/installer/install_gui.py --uninstall
python scripts/installer/install_zed.py                  # сначала должен отработать install_cli.py
python scripts/installer/install_zed.py --uninstall
python scripts/installer/install_kvide.py                # сначала должен отработать install_cli.py
python scripts/installer/install_kvide.py --uninstall
```

## Вывод

По умолчанию каждый установщик молчалив: команды, которые он запускает (pip,
PyInstaller, npm, Gradle, vsce), пишут в собственный лог во временной папке,
пока одна строка-спиннер называет текущий шаг, а при сбое печатается хвост
этого лога и его путь. `--verbose` у любого из них показывает эти команды
напрямую — без спиннера и без лога. `install_zed.py` ничего не запускает — он
правит один файл — поэтому печатает свои шаги сразу, а `--verbose` лишь
добавляет запись, которую он написал.

При запуске через `install.py` выбранные установщики идут одновременно как
фоновые процессы, а живая панель показывает, на каком шаге каждый; 
`install.py --verbose` убирает панель и гоняет их по очереди. Компонент,
которому нужен другой, ждёт его: регистрация Zed остаётся в очереди, пока
установщик CLI не положит бинарник на место.

## Как ищутся IDE JetBrains

Начиная с 2020.1 каждая IDE держит пользовательские данные под одним корнем
производителя:

* Linux: `~/.local/share/JetBrains/<Product><version>`
* Windows: `%APPDATA%\JetBrains\<Product><version>`
* macOS: `~/Library/Application Support/JetBrains/<Product><version>`

`install_jetbrains.py` перечисляет эти каталоги продуктов (IntelliJ IDEA,
PyCharm, WebStorm, GoLand, CLion, Rider, RubyMine, DataGrip, RustRover, Android
Studio, …) и ставит в `.../<Product><version>/plugins/`. После этого перезапустите
IDE.

В Linux плагин ставится прямо в `.../<Product><version>/`, без вложенной папки `plugins/`. Поиск также проверяет `~/.config/JetBrains` (или `$XDG_CONFIG_HOME/JetBrains`): у недавно запущенной IDE каталог данных плагинов может ещё отсутствовать. Для неё установщик создаёт каталог в `~/.local/share/JetBrains` (или `$XDG_DATA_HOME/JetBrains`). Ограничения на год версии нет.

## Как ищутся редакторы VS Code

`install_vscode.py` ищет команды запуска в `PATH`: `code`, `code-insiders` и
`codium` (в Windows — вместе с вариантами `.cmd`). В Windows он также проверяет
обычные пользовательские расположения и Program Files. Собранный VSIX ставится
так:

```bash
code --install-extension <kvist.vsix> --force
```

`--build` пересобирает входящий в комплект бинарник Kvist и VSIX перед
установкой, а `--vsix path/to/file.vsix` ставит указанный пакет.

## Как настраивается Zed

Zed связывается с кодящими агентами через Agent Client Protocol и находит их в
`agent_servers` файла настроек. `install_zed.py` пишет ровно одну запись этого
объекта и больше ничего:

```json
{
  "agent_servers": {
    "Kvist": {
      "type": "custom",
      "command": "/home/you/.local/share/kvist/kvist-core",
      "args": ["--acp"],
      "env": {
        "XDG_CONFIG_HOME": "/home/you/.local/share/kvist/acp-pristine-config"
      }
    }
  }
}
```

В `command` — автономный бинарник, который ставит `install_cli.py`, а не
обёртка `kvist`: она принудительно добавляет `--workspace "$PWD"` (что спорит с
рабочей папкой, которую ACP передаёт в `session/new`) и написана как shell-скрипт,
который Windows не может исполнить. Сначала запустите `install_cli.py`:
`install_zed.py` правит только настройки и отказывается писать запись, которая
указывает на отсутствующий бинарник.

`XDG_CONFIG_HOME` держит пользовательский старт вашей оболочки подальше от
stdout агента — Zed оборачивает команду логиновой оболочкой, и вывод любого
старта заставляет Zed отвергнуть поток. `install_zed.py` создаёт этот каталог
рядом с бинарником; сам механизм описан в
[`frontends/zed/README.ru.md`](../../frontends/zed/README.ru.md).

Настройки Zed — это JSONC, поэтому правка заменяет ровно тот диапазон символов,
который занимает одна запись (`jsonc_settings.py`). Комментарии, порядок ключей
и переводы строк сохраняются, а файл переписывается атомарно с прежними
правами доступа. Перед первым изменением он копируется в
`settings.json.kvist-backup` — один раз, чтобы последующий запуск не затёр ваши
правки, сделанные с тех пор.

Zed документирует `~/.config/zed/settings.json` для macOS и для Linux без своего
`XDG_CONFIG_HOME`, и перечитывает файл сам («You do not need to restart Zed.»).
Расположение для Windows **не** документировано — сказано лишь, что настройки
живут в пользовательском профиле, — поэтому в Windows установщик проверяет
известные ему кандидатов и, если ни одного нет, останавливается и перечисляет
их, а не выдумывает путь. `--settings` указывает на файл, который Zed реально
использует.

Какой паритет с интеграциями для VS Code и JetBrains у этой есть, а какого нет,
написано в [`frontends/zed/README.ru.md`](../../frontends/zed/README.ru.md).

## Иконки (`branding/`)

Установщики берут иконки из каталога `branding/` в корне репозитория.
`icon.svg` — исходный знак (циановый шеврон и курсорная черта на сланцевом
скруглённом квадрате). `icon-<n>.png` — плоские рендеры; `kvist.ico` —
многоразрешённая иконка для Windows. Перегенерация из SVG:

```bash
cd branding/
for s in 16 24 32 48 64 128 256 512; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done
magick icon-256.png -define icon:auto-resize=256,128,64,48,32,16 kvist.ico
```

Копии этих файлов для сборки GUI лежат в `frontends/gui/electron/build/` и подключены в
`frontends/gui/electron/package.json` (`build.linux.icon`, `build.win.icon`).
