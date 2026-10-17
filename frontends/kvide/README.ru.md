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
kvide --generators ~/.local/share/kvist/kvide/generators.toml
```

Откройте панель коммита (`Ctrl+G`), выберите файлы, нажмите `Kvist` или
`Ctrl+F1`. Генерация никогда не делает коммит. `install_kvide.py --uninstall`
удаляет каталог `kvide/` рядом с бинарником и больше ничего.

Что адаптер делает со входом kvide (`{"version", "files", "diff"}`):

- режет `diff` на патчи по файлам (секции `diff --git` и блоки
  `New file: <path>` от kvide), потому что ядро отбрасывает секретные пути и
  `.kvist/` по одному файлу;
- добавляет последние 15 заголовков коммитов из `git log`, чтобы сообщение
  совпадало со стилем репозитория;
- превращает `{"ok": false, "error": ...}` ядра в конверт kvide
  `{"error": {"code", "message"}}`.

Тесты: `frontends/kvide/tests`, `scripts/installer/tests/test_install_kvide.py`.
