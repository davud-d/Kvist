# веб-клиент kvist: аудит первого запуска

Фаза A задачи «новичок с ключом AnyModel отправляет первую задачу». Код клиента в этой фазе не менялся; все наблюдения сняты с коммита 2707b56 (ветка web-redesign, до правки фазы B в 1c400e8). Спецификация решений: [web-onboarding-spec.md](web-onboarding-spec.md).

## как проводился проход

- стенд: [web-onboarding/audit/stand.py](web-onboarding/audit/stand.py). Настоящий контроллер kvist-web (HTTP, CSRF, SSE, процессы ядра, запись конфигурации ядром) на временном HOME и временном воркспейсе `my-project`. Каталог `~/.kvist` и файлы окружения не читались.
- ключ: только тестовый `qa-valid-key`. Адреса AnyModel и DeepSeek указывают на фальшивый OpenAI-совместимый сервер на loopback (он же отвечает на задачу), проверка ключа AnyModel подменена той же проверкой. `HTTP(S)_PROXY` указывает на мёртвый порт, поэтому ни один запрос не уходит наружу. Страница регистрации anymodel.org в браузере подменена заглушкой.
- сценарий: [web-onboarding/audit/audit.mjs](web-onboarding/audit/audit.mjs), Playwright и Chromium 1243, headless. Прогон дважды: 1440x900 и 390x844, каждый на новом стенде.
- действие: один клик, одно заполненное поле или одно нажатие клавиши, которое делает человек. Ожидания не считаются. Открыть выпадающий список и выбрать пункт это два действия.
- «неочевидно за 5 секунд»: оценка по снимку шага, что человек без документации не увидит следующий шаг на экране за 5 секунд. Это суждение аудитора, а не замер.
- снимки каждого шага: `web-onboarding/audit/desktop/` и `web-onboarding/audit/mobile/`, имя `NN-tK-шаг-действие.png`. Воспроизведение ошибки фазы B: `web-onboarding/audit/phase-b/{before,after}/`.
- скилл frontend-design загружен. Визуальное направление задано спекой редизайна (тёмная тема, токены oklch, один акцент, раздел «принципы» в frontends/web-local/web-ui-spec.md), поэтому скилл применён к структуре и текстам, а не к палитре.

## число действий сейчас

| задача | 1440 | 390 | дошёл до цели |
|---|---|---|---|
| 1. запуск с нуля | 1 команда в терминале + страница открывается сама | то же | да |
| 2. подключение провайдера (AnyModel) | 6 (минимум 5) | 6 (минимум 5) | да |
| 3. выбор модели | 4 | 4 | да |
| 4. смена режима рассуждений | 4 | 4 | да, но результат нигде не виден |
| 5. поиск списка команд | 2 | 4 | частично: в списке нет модели, рассуждений и роли |
| 6. отправка первой задачи | 2 | 2 | да |
| 7. смена модели посреди сессии | 3 | 3 | нет: ядро сменило модель, интерфейс этого не показал |

Путь новичка от открытой страницы до отправленной задачи (задачи 2 и 6): 8 действий наблюдаемо, 7 при знании, что модель обязательна. Цель задачи: 4.

После фазы B (1c400e8) задача 2 для AnyModel: вставить ключ, открыть список моделей, выбрать, сохранить, то есть 4 действия (каталог грузится сам, когда фокус уходит с поля ключа); задача 3: 4 действия (кнопка модели, открыть список, выбрать, сохранить; каталог по сохранённому ключу грузится при открытии). Повторного прохода по всем задачам после фазы B не было.

## повторный проход после реализации

Тот же стенд и те же семь задач на чистом профиле после коммитов 0bf8f5f..cb8b6e5, сценарий [web-onboarding/audit/audit-after.mjs](web-onboarding/audit/audit-after.mjs), снимки каждого шага в `web-onboarding/audit/after/desktop/` и `web-onboarding/audit/after/mobile/`. На обеих ширинах числа одинаковые, ошибок страницы нет.

| задача | до (2707b56) | после | что изменилось |
|---|---|---|---|
| 1. запуск с нуля | 1 команда, о ней нигде не сказано | 1 команда | `kvist --help` и README называют `kvist-web`; экран первого запуска показывает рабочую папку до старта |
| 2. подключение провайдера | 6 | 4: вставить ключ, открыть список, выбрать модель, Start | ключ проверяется при вставке; со списком моделей AnyModel по умолчанию 2 (вставить, Start), проверено onboarding.mjs |
| 3. выбор модели | 4 | 2: чип, пункт | меню над полем ввода |
| 4. режим рассуждений | 4, результат не виден | 2: чип, пункт | уровень виден на чипе |
| 5. список команд | 2 (на 390: 4), без модели, рассуждений и роли | 1: «/» | меню из восьми команд; кнопка «/» и «⋮ Commands» в шапке |
| 6. первая задача | 2 | 2 | пустая сессия с четырьмя стартовыми задачами и подсказкой про «/» |
| 7. смена модели в сессии | 3, результат не виден | 2, подпись меняется за 0,3 с, тост «Models updated.» | `command_result` больше не теряется |
| путь до первой задачи (2 и 6) | 8 | 6 (4 со списком моделей по умолчанию) | список `ANYMODEL_DEFAULT_MODELS` ждёт id от владельца |

## журнал трения

### задача 1. запуск с нуля
- `kvist-web` ставится установщиком (scripts/installer/install.py:67, обёртка install_cli.py:98-134) и лежит в `.venv/bin` checkout-а. Запуск на чистом HOME: страница отдаётся с кодом 200, браузер открывается сам (frontends/web-local/kvist_web/cli.py:111-112).
- трение: корневой README.md и README.ru.md о `kvist-web` не пишут (поиск `kvist-web|web-controller` по ним пуст), `kvist --help` веб-клиент не упоминает (флаг `--web-controller` разбирается в kernel/__main__.py:23 до argparse). Новичок, который поставил только `kvist`, о веб-клиенте не узнает. Запуск из исходников требует отдельного `pip install -e .` (frontends/web-local/README.md:19-27).
- трение: воркспейс по умолчанию это текущий каталог (`--workspace` по умолчанию `os.getcwd()`, cli.py:43). Запуск из домашнего каталога делает воркспейсом весь HOME, и экран об этом не предупреждает: в шапке только путь.
- трение: строка `Kvist web is running at …` при выводе не в терминал буферизуется и не видна (stdout пуст через 5 с при запуске в фоне), адрес приходится угадывать.
- снимки: `01-t1-1-open-url.png` (заставка «Restoring your workspaces…»), `01-t1-2-app-ready.png`.

### задача 2. подключение провайдера
Шаги: вставить ключ, Save & continue (отказ: поле модели пусто), Load available models, открыть поле модели, выбрать модель, Save & continue.
- неочевидно: окно открывается само (app.js:185, `refreshConfiguration(undefined, true)`) и сразу просит ключ, но модель обязательна (`required`, configuration.js@2707b56:51), а список моделей AnyModel пуст до нажатия Load available models. Первая попытка сохранить падает с браузерной подсказкой «Please fill out this field.» (`02-t2-3-save-without-model.png`, на 390 подсказка перекрывает Advanced).
- неочевидно: Load available models выглядит как подпись, а не кнопка (quiet-button без рамки, `02-t2-1-modal-visible.png`).
- неочевидно: после загрузки подпись «3 models available. Choose a model below.» стоит под полем модели и под Advanced, то есть модель выше, а не ниже (`02-t2-6-open-model-field.png`).
- неочевидно: модель выбирается из `<datalist>`: поле выглядит как обычный текст, список виден только после клика по стрелке или ввода (configuration.js@2707b56:51-52).
- дефект вёрстки: кнопка Open AnyModel registration ↗ наезжает на последнюю строку абзаца над ней на 1440 и 390 (`02-t2-1-modal-visible.png`, `mobile/02-t2-1-modal-visible.png`).
- после сохранения тост «Provider connected. You can send your first message.» на 390 закрывает поле ввода и кнопку отправки целиком, на 1440 закрывает селектор модели (`mobile/02-t2-9-modal-closed.png`, `desktop/02-t2-9-modal-closed.png`).
- окно называется «Connect a model provider», но провайдер AnyModel уже выбран, а слово «provider» новичку ничего не говорит; что такое AnyModel и почему он первый, не сказано.

### задача 3. выбор модели
Шаги: кнопка модели в поле ввода, Load available models, ввести модель, Save defaults.
- неочевидно: кнопка модели в поле ввода подписана id модели моноширинным шрифтом без подсказки, что это меню (`02-t2-9-modal-closed.png`). Спека требует `--sans` для селектора модели (web-ui-spec.md:19), а `.model-button` стоит `font-family: var(--mono)` (styles.css:1074).
- неочевидно: при повторном открытии список AnyModel пуст (0 вариантов), его надо снова загрузить кнопкой (`03-t3-2-form.png`).
- непонятно, к чему применяется выбор: кнопка Save defaults и подпись «New sessions use these defaults. Existing sessions keep their current provider and models.» (configuration.js@2707b56:55) говорят, что текущая сессия не изменится.

### задача 4. смена режима рассуждений
Шаги: кнопка модели, раскрыть Advanced · models, reasoning & endpoint, выбрать уровень, Save defaults.
- неочевидно: режим рассуждений спрятан в раскрывающемся блоке окна модели (configuration.js@2707b56:53), рядом с адресом провайдера (`04-t4-3-open-advanced.png`). На главном экране уровень не виден ни до, ни после смены (проверено по тексту `main`).
- уровни подписаны только именами `false|low|…|max` (`Off` вместо `false`); описаний уровней нет, хотя ядро их отдаёт (kernel/llm/reasoning_effort.py:53-60, событие manifest kernel/protocol/core_manifest.py:36-47).

### задача 5. поиск списка команд
Шаги на 1440: ввести «/» (ничего), кнопка Commands в подвале сайдбара. На 390 ещё открыть шторку и закрыть её после палитры.
- неочевидно: «/» в поле ввода ничего не открывает; слэш-команд в веб-клиенте нет вообще, `/models` уйдёт агенту как задача (в app.js нет разбора «/», отправка app.js:281-330).
- кнопка Commands есть (index.html:17, `⌘ / Ctrl K Commands`), но внизу сайдбара мелким шрифтом, а на 390 спрятана в шторке.
- в палитре 15 команд (palette.js:5), из них нет модели, режима рассуждений, роли и провайдера: поиск «model», «reason», «role», «provider» даёт пусто. Зато есть Switch server и Connect server, хотя удалённый режим заморожен (SHOW_SERVERS = false).
- на 390 после закрытия палитры шторка остаётся открытой и закрывает поле ввода (`mobile/05-t5-7-escape.png`); первый прогон сценария на этом месте не смог нажать отправку за 30 с, пока не добавили тап мимо шторки (`mobile/05-t5-8-tap-outside-drawer.png`).
- роль нельзя сменить из веб-клиента никак: контроллер пропускает только команды `models` и `effort` (frontends/web-local/kvist_web/processes.py:134-136).
- Settings в сайдбаре это только синхронизация с удалённым сервером и текст «Connect a server from the Local selector to enable configuration sync.», хотя селектора Local на экране нет (connections.js:201-210). Новичок ищет модель и ключ в Settings и не находит.

### задача 6. отправка первой задачи
Шаги: ввести задачу, отправить.
- пустое состояние «Start a conversation / Give Kvist a task in your workspace.» (index.html:21) не предлагает ни одной стартовой задачи и не говорит про «/» (`02-t2-9-modal-closed.png`).
- подсказка «Ctrl+Enter to send» есть; Enter переносит строку, это расходится с TUI и VS Code, где Enter отправляет.
- заголовок сессии на стенде показан с сырой разметкой `**my-project**` (`06-t6-3-answer.png`). Заголовок сочинила фальшивая модель стенда, поэтому это артефакт стенда; но клиент выводит заголовок как есть, и та же разметка от настоящей модели будет видна так же. Не проверялось, чистит ли ядро заголовок.

### задача 7. смена модели посреди сессии
Шаги: кнопка модели, ввести модель, Use model in this session.
- ошибка: ядро меняет модель и отвечает `command_result` с `ok: true`, `message: "Models updated."` и `effects.models.fast: "gpt-5.6-luna"` (поймано отдельным EventSource на стенде), но подпись модели остаётся прежней и тоста «Models updated.» нет (ожидание 30 с, `07-t7-5-confirmed.png`). Причина: контроллер публикует `command_result` как durable, потому что его нет в `_TRANSIENT_CORE_EVENTS` (processes.py:25-34); клиент применяет живьём только не-durable события и пять исключений `LIVE_DURABLE` (blocks.js:38-39), а для durable ждёт записи журнала, которой нет: журнал ядра `command_result` не пишет (в `.events.jsonl` стенда только user, route, stream_start, stream_commit, usage, turn_end). Обработчик с подписью и тостом (session.js:221-226) не вызывается.
- после команды текущая открытая сессия получила точку непрочитанного и бейдж Inbox 1 (`07-t7-4-use-in-session.png`).
- тост «Changing model… The core will confirm the result.» обещает подтверждение, которое не приходит.

## пять главных находок

1. Модель и режим рассуждений нельзя ни увидеть, ни сменить на главном экране: всё в одном окне за кнопкой с id модели, рассуждения за Advanced, роль недоступна вовсе (processes.py:134-136).
2. Смена модели посреди сессии молча не отражается в интерфейсе: `command_result` теряется между controller (durable) и клиентом (blocks.js:38-39).
3. Первый запуск это обычное окно настроек: модель обязательна, но список пуст до кнопки, похожей на подпись; первая попытка сохранить всегда падает. Путь до первой задачи 8 действий при цели 4.
4. Команд в поле ввода нет: «/» ничего не делает, палитра не знает модель, рассуждения, роль и провайдера, а Settings говорит про несуществующий селектор.
5. О веб-клиенте нельзя узнать из `kvist --help` и корневого README; воркспейс молча равен текущему каталогу.

Мелкие дефекты по ходу: наезд кнопки регистрации на текст, тост поверх поля ввода и кнопки отправки на 390, шторка не закрывается после палитры на 390, селектор модели моноширинный вопреки спеке.

## таблица паритета

| функция | TUI (Ink и классический терминал) | VS Code | веб (2707b56) |
|---|---|---|---|
| первый запуск | Ink без провайдера не стартует и отсылает в классический терминал (kernel/main.py:813-819); там экран AnyModel: Enter открывает регистрацию, Esc другой провайдер, вставка ключа и проверка через `/v1/models` (kernel/llm/anymodel_onboarding.py:131-180, main.py:1094-1105) | отдельный экран до запуска ядра: `--config-info`, карточки провайдеров с подписью, AnyModel первым (src/chatView.ts:143-164, 19; webview/firstRun.tsx:47-156) | окно поверх приложения после загрузки (app.js:185), тот же контент, что окно настроек (configuration.js) |
| регистрация AnyModel | `ANYMODEL_REGISTER_URL` (anymodel_onboarding.py:32), открывает браузер или печатает адрес | `--config-op open-registration` открывает автоматически один раз, без браузера показывает адрес (chatView.ts:183-186, firstRun.tsx:63-67, 98-106) | ссылка `target=_blank` с адресом из `registration_url` ядра (configuration.py:40, configuration.js@2707b56:62-64); при заблокированной вкладке ничего не сообщает |
| проверка ключа | `fetch_model_coefficients` до сохранения (anymodel_onboarding.py:171-178) | `--config-op set-provider` | та же проверка при сохранении (frontends/web-local/kvist_web/configuration.py:120-126) |
| выбор провайдера | `choose_provider` список с фильтром по вводу | 7 карточек, остальные в Settings | `<select>` из 43 провайдеров |
| список моделей | `/models`: живой `/v1/models` по сохранённому ключу, иначе встроенные варианты (main.py:2158-2211); для ручных провайдеров меню «из каталога / ввести id» (main.py:2952-2990) | пилюля модели запускает команду ядра `models`, то есть тот же мастер (webview/app.tsx:443); на первом запуске для провайдера без моделей поле с datalist и Load models через `--config-op list-models` (firstRun.tsx:118-147, main.py:345-369) | поле с `<datalist>`, живой каталог только по кнопке (configuration.js@2707b56:50-52, 92-105); после 1c400e8 список, как в TUI |
| режим рассуждений | `/effort` со списком и описанием уровня (main.py:1844-1870); в claude-скине строка `● high · /effort` над полем (ui/skins/claude-code/claude.tsx:568-572) | пилюля уровня в поле ввода запускает `effort` (app.tsx:444), сегменты в Settings (webview/settings.tsx:170) | в Advanced окна модели (configuration.js@2707b56:53), на экране не виден |
| роль | `/role` со списком и описаниями (main.py:1791-1843); роль в подвале во время хода (claude.tsx:552-554) | сегменты в Settings (settings.tsx:166) | нет; контроллер её не пропускает (processes.py:134-136) |
| список команд | меню подсказок над полем при «/» (commands.ts:15-33, 73; ui/input.tsx:211-259), подсказки Tip под спиннером (ui/skins/claude-code/tips.ts) | меню при «/» из того же `commands.ts`, плейсхолдер «Ask Kvist, or type / for commands» (app.tsx:4, 377-386, 428) | нет «/»; палитра Ctrl/Cmd+K без модели, рассуждений и роли (palette.js:5) |
| пустая сессия | баннер и Tip | 3 стартовые задачи и недавние сессии (app.tsx:24, 350-366) | «Start a conversation» без стартов (index.html:21) |
| смена модели посреди сессии | `/models`, `command_result` применяется сразу | пилюля, `command_result` | кнопка Use model in this session; результат не показывается (blocks.js:38-39) |
| данные для меню | событие `manifest`: роли, уровни с описаниями, провайдеры, команды (core_manifest.py:36-47) | использует | отбрасывает (`DROPPED`, blocks.js:41) |

## паттерны

Материалы 2025-2026 годов, кроме отмеченного. Ссылки проверены 2026-10-07.

1. Меню команд по «/» с фильтром по вводу. Claude Code: «Type / … to see the commands available to you, or type / followed by any letters to filter» ([code.claude.com/docs/en/interactive-mode](https://code.claude.com/docs/en/interactive-mode)); Codex CLI так же ([developers.openai.com/codex/cli/slash-commands](https://developers.openai.com/codex/cli/slash-commands), сейчас редирект на learn.chatgpt.com). Берём: это главный способ найти команды в TUI и VS Code, человек из терминала его ждёт.
2. Команда срабатывает только в начале сообщения, «/» внутри текста и путь `/tmp/a` меню не держат (тот же раздел Claude Code). Берём: иначе пути в задаче будут открывать меню.
3. Выбор модели и уровня рассуждений в одном пикере: в `/model` стрелки влево и вправо двигают ползунок effort ([code.claude.com/docs/en/model-config](https://code.claude.com/docs/en/model-config)); в Codex `/model` предлагает пары «модель + effort» ([developers.openai.com/codex/cli/slash-commands](https://developers.openai.com/codex/cli/slash-commands)). Не берём слияние: в Kvist уровень глобальный и не зависит от модели (`reasoning_effort` в конфиге), а у части провайдеров он вообще транслируется иначе; два отдельных меню рядом честнее.
4. Две силы применения: Enter сохраняет как умолчание, `s` только для этой сессии ([code.claude.com/docs/en/model-config](https://code.claude.com/docs/en/model-config)). Не берём второй режим: команда `models` ядра уже сохраняет выбор как умолчание (main.py:3236-3290), отдельный режим «только сессия» потребовал бы новой команды ядра. Берём явную подпись в меню: «Applies now and to new sessions».
5. Текущие модель и уровень видны постоянно: «The current effort level is also shown in the session header next to the model name» (там же). Берём: модель, рассуждения и роль в строке у поля ввода.
6. Селектор модели прямо в поле ввода, с клавишей: Zed «click on the model selector on the message editor or by using the cmd-alt-/» ([zed.dev/docs/ai/agent-panel](https://zed.dev/docs/ai/agent-panel)); Cursor «Open the model selector in your chat or agent panel» ([cursor.com/help/models-and-usage/available-models](https://cursor.com/help/models-and-usage/available-models)). Берём место; горячую клавишу не берём в первой версии: Ctrl+/ и Alt+P в браузере заняты или конфликтуют с раскладками, «/» и палитры хватает.
7. Избранные модели и перебор без открытия меню (Zed, звёздочка и alt-tab, там же; Cursor Cmd+/). Не берём: у новичка одна модель, а длинный каталог AnyModel решается фильтром в меню.
8. Режим Auto, где сервис сам выбирает модель ([cursor.com/help/models-and-usage/available-models](https://cursor.com/help/models-and-usage/available-models)). Не берём для модели: в ядре нет маршрутизатора моделей. Берём для роли: у Kvist уже есть `auto` (роутер ролей), это пункт по умолчанию в меню роли.
9. Подключение провайдера как команда: OpenCode `/connect`, поиск провайдера, вставка ключа, затем `/models` ([opencode.ai/docs/providers](https://opencode.ai/docs/providers)). Берём: «Add provider…» внизу меню модели и команда в «/» и палитре.
10. Первый запуск с выбором способа входа и рекомендованным путём первым: Codex предлагает вход через ChatGPT, device code и свой API-ключ ([analyticsvidhya.com/blog/2026/08/how-to-install-codex-cli](https://www.analyticsvidhya.com/blog/2026/08/how-to-install-codex-cli/); quickstart Codex: «may also use Codex with an API key», [learn.chatgpt.com/docs/quickstart?setup=cli](https://learn.chatgpt.com/docs/quickstart?setup=cli)). Берём: AnyModel основной путь, «I have another provider» второй, видимый сразу.
11. Ключ проверяется сразу и показывается причина отказа: валидация отвечает 200 для рабочего ключа и 401 для отозванного, «so bring-your-own-key integrations can confirm a key and show why it failed» ([developers.toolpath.com/reference/validate-key](https://developers.toolpath.com/reference/validate-key)). Берём: проверка при вставке существующим механизмом ядра (`fetch_model_coefficients`, `model_catalog`), текст ошибки говорит, что делать.
12. Минимум шагов между входом и первым успешным вызовом: «the fewest possible steps between landing and a successful call» ([faq.apievangelist.com/questions/what-makes-good-api-onboarding](https://faq.apievangelist.com/questions/what-makes-good-api-onboarding/)). Берём: цель 4 действия, модель по умолчанию после ключа.
13. Пустое состояние сообщает статус, учит и даёт прямой путь к ключевому действию (NN/g, «Use Empty States to Provide Direct Pathways for Key Tasks», [nngroup.com/articles/empty-state-interface-design](https://www.nngroup.com/articles/empty-state-interface-design/), статья 2021 года; источник старше окна 2025-2026, взят как исходная формулировка). Берём: 3-4 стартовых действия и подсказка про «/».
14. Пустой экран как «teachable moment»: человек здесь решает, понял ли он продукт ([setproduct.com/blog/empty-state-ui-design](https://www.setproduct.com/blog/empty-state-ui-design), декабрь 2025, обновлено июнь 2026). Берём: учим на месте (пустая сессия, плейсхолдер, подпись пункта меню), без тура и оверлеев поверх интерфейса.
15. Каталог моделей читается при старте, и после подключения провайдера нужен перезапуск: «Fully quit and restart OpenCode. The provider catalog is read at startup» ([openrouter.ai/docs/cookbook/coding-agents/opencode-integration](https://www.openrouter.ai/docs/cookbook/coding-agents/opencode-integration), без даты). Не берём, это антипаттерн: каталог грузится при вводе ключа и при открытии меню (так уже делает 1c400e8).
