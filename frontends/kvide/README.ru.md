# Генератор сообщений коммита для kvide

[kvide](../../../kvide) показывает изменённые файлы в панели коммита и запускает
генераторы из TOML-файла, переданного через `--generators`. `kvist_generator.py`
— такой генератор для Kvist: он отдаёт выбранные изменения в
`kvist-core --commit-message` (ту же команду использует кнопка коммита в
JetBrains) и кладёт ответ в поле сообщения. Провайдер, быстрая модель, фильтр
защищённых путей и бюджет токенов принадлежат ядру; адаптер лишь переводит один
JSON в другой, поэтому ему нужна только стандартная библиотека.

```bash
python scripts/installer/install_cli.py     # ставит kvist-core
python scripts/installer/install_kvide.py   # или: install.py --kvide
kvide                                       # кнопка Kvist уже на месте
```

Установщик пишет пользовательский файл kvide (`$XDG_CONFIG_HOME/kvide/generators.toml`,
иначе `~/.config/kvide/`; `%APPDATA%\kvide` в Windows), который kvide читает без
`--generators`. Чужой файл (без первой строки `# managed by kvist`) не
перезаписывается и не удаляется; тогда установщик кладёт копию в
`~/.local/share/kvist/kvide/generators.toml` и печатает команду с
`kvide --generators`.

Откройте панель коммита (`Ctrl+G`), выберите файлы, нажмите `Kvist` или
`Ctrl+F1`. Генерация никогда не делает коммит. `install_kvide.py --uninstall`
удаляет каталог `kvide/` рядом с бинарником и пользовательский файл, если его
писал Kvist.

Что адаптер делает со входом kvide (`{"version", "files", "diff"}`):

- режет `diff` на патчи по файлам (секции `diff --git` и блоки
  `New file: <path>` от kvide), потому что ядро отбрасывает секретные пути и
  `.kvist/` по одному файлу;
- добавляет последние 15 заголовков коммитов из `git log`, чтобы сообщение
  совпадало со стилем репозитория;
- превращает `{"ok": false, "error": ...}` ядра в конверт kvide
  `{"error": {"code", "message"}}`.

Тесты: `frontends/kvide/tests`, `scripts/installer/tests/test_install_kvide.py`.

## Автокомплит

`completion-extension/` — inline-расширение для kvide. Оно запускает
`kvist-core --complete-server` (тот же процесс, что у плагинов IDE) и показывает
одну подсказку серым текстом после курсора, как в VS Code: Tab принимает, Esc
убирает, ввод тех же символов укорачивает её. Показывается и вставляется только
первая строка; следующая подсказка приходит после очередной паузы.

```bash
python scripts/installer/install_kvide.py   # заодно копирует расширение и записывает путь к ядру
kvide --completion-extension ~/.local/share/kvist/kvide/completion-extension
```

У kvide нет пользовательского файла для расширений, а расширение — это JavaScript
с твоими правами, поэтому оно включается только этим флагом. `KVIST_CORE`
переопределяет записанный путь к ядру. Первая подсказка ждёт запуска ядра
(около 1,5 с) и модели; ответ дольше 12 с отбрасывается без сообщения.
Тесты: `frontends/kvide/tests`.
