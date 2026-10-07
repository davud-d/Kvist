# веб-клиент kvist: первый запуск, модель, рассуждения, роль, команды

Спецификация фазы C. Цель: новичок с ключом AnyModel отправляет первую задачу не больше чем за 4 действия после открытия страницы; модель, режим рассуждений, роль и список команд видны и меняются без знания слэш-команд. Основание: [web-onboarding-audit.md](web-onboarding-audit.md). Прототип: [web-onboarding/prototype/](web-onboarding/prototype/) (статический, вне сборки клиента, открывается `index.html` или `app.html`). Снимки всех состояний: `web-onboarding/prototype/shots/{1440,390}/<состояние>.png`. Код клиента в этой фазе не менялся; реализация не начата.

## рамки
- меняются frontends/web-local/kvist_web/client/ и tests/browser/. Вне клиента три правки в пакете kvist_web, без ядра: `configuration.py` (новые поля ответа и поле `role` при сохранении), `processes.py` (команда `role` и `command_result` как transient). HTTP API v1 расширяется только новыми полями, `protocol_version` 1 и UI_API.md не меняются: команды `models`, `effort`, `role` уже есть в stdio-multi (kernel/main.py:3049-3105, 3236-3290).
- одно решение за владельцем, в ядре: модель AnyModel по умолчанию (раздел «открытые вопросы», пункт 1). Без него первый запуск занимает 6 действий, а не 4.
- csp style-src 'self' и script-src 'self' без изменений: прототип снят под тем же заголовком CSP, что отдаёт http_api.py:413-415, нарушений нет (`prototype/shoot.mjs` падает на нарушении).
- стили: только токены :root из styles.css; нет text-transform, box-shadow, gradient, blur, цветовых литералов вне :root и внешних адресов. Новых цветовых токенов нет (их 12 из 14).
- тексты интерфейса на английском, предложения с заглавной буквы, без жаргона («provider» только там, где без него нельзя). Описания уровней рассуждений и ролей берутся из ядра как есть (kernel/reasoning_effort.py:53-60, kernel/role_router.py:40-46 через kernel/core_manifest.py:36-47), своих копий клиент не держит.

## число действий

Действие: один клик, одно заполненное поле, одно нажатие клавиши человеком. Открыть выпадающий список и выбрать пункт это два действия.

| задача | сейчас (2707b56) | по спецификации |
|---|---|---|
| 1. запуск с нуля | 1 команда, о которой нигде не сказано | 1 команда (`kvist-web`), о ней говорят README и `kvist --help` |
| 2. подключение провайдера | 6 (минимум 5) | 2: вставить ключ, Start (4 без модели AnyModel по умолчанию) |
| 3. выбор модели | 4 | 2: чип модели, пункт |
| 4. смена режима рассуждений | 4 | 2: чип рассуждений, пункт |
| 5. поиск списка команд | 2 (на 390: 4), без модели, рассуждений и роли | 1: «/» в поле или кнопка «/» |
| 6. отправка первой задачи | 2 | 2: стартовое действие или текст, отправка |
| 7. смена модели посреди сессии | 3, результат не виден | 2: чип модели, пункт; подтверждение видно |
| путь до первой задачи (2 и 6) | 8 | 4 (6 без модели по умолчанию) |

## экран 1. первый запуск
Снимки: `first-run-*.png`.

Когда. Вместо окна «Connect a model provider» поверх приложения (app.js:185) экран занимает основную колонку, пока `GET /api/v1/configuration` выбранного сервера отвечает `setup_required: true`. Поле ввода и пустое состояние скрыты, сайдбар остаётся (воркспейс виден). Экран не закрывается Escape: без провайдера закрывать нечего. При `available: false` (старый мост) экран говорит «This server runs an older Kvist. Update it to set up a model here.».

Раскладка. Колонка 480px по центру основной области, текст по левому краю, отступ сверху 12vh (на 390: 32px, поля 16px). Заголовок 20px/600, подзаголовок 14px `--text-2`. Нумерованный список из двух шагов, потому что это последовательность: сначала ключ, потом его вставка. Поле ключа единственный крупный элемент экрана: высота 44px, 15px, рамка `--line-strong`, в фокусе `--accent`. Кнопка Start это существующий `.primary-button`; акцент на экране не используется, кроме фокуса.

Тексты:
- заголовок «Connect a model»; подзаголовок «Kvist sends your tasks to a language model. AnyModel gives you one key for many models.»
- шаг 1 «Get a key», кнопка «Create an AnyModel account»; после нажатия строка «Registration opened in a new tab. Create a key there and paste it below.»
- шаг 2 «Paste the key», поле с плейсхолдером «Paste your AnyModel key».
- «Model» (список появляется после проверки ключа), кнопка «Start», текстовая кнопка «I have another provider».

Основной путь AnyModel:
1. кнопка регистрации: `window.open(registration_url, '_blank', 'noopener')`. Адрес только из поля `registration_url` ответа конфигурации, то есть из `ANYMODEL_REGISTER_URL` ядра (kernel/anymodel_onboarding.py:32, frontends/web-local/kvist_web/configuration.py:40); клиент принимает только https (как сейчас, configuration.js:65-66). Фокус после нажатия переходит в поле ключа.
2. ключ проверяется сам: по событию paste (после вставки), по change и по Enter в поле. Проверка это существующий `POST /api/v1/configuration/models` (configuration.py:85-95, тот же `fetch_available_models`, что у TUI, kernel/main.py:2158-2177) для каталога и `fetch_model_coefficients` при сохранении (configuration.py:120-126, kernel/anymodel_onboarding.py:171-178). Новой проверки не появляется.
3. при успехе: строка «The key works. N models available.», список Model с выбранной моделью, фокус на Start. Выбор модели по порядку: сохранённая модель провайдера, `defaults.fast` из конфигурации, иначе пустой пункт «Choose a model» и Start выключен до выбора.
4. Start сохраняет ключ, модель (обе роли fast и smart одной моделью, как первый запуск VS Code, webview/firstRun.tsx:53-55) и текущий уровень рассуждений через существующий `POST /api/v1/configuration`, затем экран уходит, открывается пустая сессия (экран 2) с фокусом в поле ввода.

Второй путь «I have another provider»: форма на месте первой (снимок `first-run-other*.png`): Provider (нативный select; первыми шесть основных провайдеров в порядке `_BUILTIN_PROVIDERS` ядра, kernel/config_info.py:46-56, затем `<optgroup label="More providers">` с остальными), «<Provider> API key», Model (список встроенных вариантов сразу, живой каталог после ввода ключа, «Other model ID…» открывает поле ввода; это поведение уже в 1c400e8), Provider address для провайдеров с `can_edit_base_url`, если адрес обязателен, Start, текстовая кнопка «Use AnyModel instead». Проверка ключа при Start тем же `save_configuration`, который сам вызывает каталог (configuration.py:127-128).

Состояния:
- загрузка конфигурации: заставка «Restoring your workspaces…» как сейчас (index.html:9).
- проверка ключа: кольцо 10px (как у соединения) и «Checking the key…»; поле остаётся редактируемым, Start выключен (`first-run-checking.png`).
- неверный ключ: рамка поля `--err`, `aria-invalid="true"`, крестик и «AnyModel did not accept this key. Copy it again from your AnyModel account.» Ничего не сохраняется (`first-run-invalid-key.png`).
- нет сети или провайдер недоступен: «Couldn't reach AnyModel. Check your connection, then try again.» с кнопкой «try again» (`first-run-offline.png`). Различить отказ ключа и недоступность можно: каталог отвечает своей ошибкой, а `fetch_model_coefficients` бросает `AnyModelAuthError` или `AnyModelUnavailableError` (anymodel_onboarding.py:173-177); `configuration.py` должен отдать вид ошибки полем `error_kind: "auth" | "unavailable"`, текст ошибки провайдера в браузер не идёт (как сейчас, configuration.py:91-94).
- пустой каталог: «The key works. AnyModel returned no model list, so enter the model ID.» и поле Model ID вместо списка (`first-run-empty-catalog.png`).
- каталог не загрузился у второго пути: встроенные варианты провайдера остаются, строка «Couldn't load models from <Provider>. Check the key, or pick a suggested model.» (`first-run-other-error.png`).
- заблокированная вкладка: если `window.open` вернул null, строка `--err` «Your browser blocked the new tab. Open anymodel.org/app/register yourself, then come back here.», где адрес это ссылка на тот же `registration_url` (хост и путь выводятся из него, не пишутся текстом) (`first-run-tab-blocked.png`).

Клавиатура и фокус. При появлении экрана фокус в поле ключа. Tab: кнопка регистрации, поле ключа, Model, Start, «I have another provider». Enter в поле ключа проверяет его, Enter на Start сохраняет. Весь путь проходится без мыши: вставка, Enter, Enter (проверено `prototype/keyboard.mjs`). Кольцо фокуса 2px `--accent` у всех контролов, у поля ключа вместо кольца рамка `--accent`.

## экран 2. пустая сессия
Снимки: `session-empty.png`, `session-drawer.png`.

- заголовок 16px/600 «What should Kvist do in <воркспейс>?», имя воркспейса из последнего сегмента пути.
- четыре стартовых действия строками-кнопками на всю ширину колонки 480px, разделённые линией `--line`: «Explain how this project is organized», «Find and fix a failing test», «Review my uncommitted changes», «Add a README section on how to run it». Нажатие вставляет текст в поле ввода и ставит туда фокус, не отправляет: человек видит, что уйдёт агенту, и может поправить.
- под ними строка 12px `--text-3`: «Type / for commands, such as changing the model.», «/» в рамке `kbd`.
- плейсхолдер поля «Message Kvist, or type / for commands» (пока агент занят, прежний «Draft your next message…», web-ui-spec.md:107).
- блок уходит, как только в сессии появилось первое сообщение; анимация появления как у прежнего пустого состояния (web-ui-spec.md:80).
- состояния загрузки, ошибки и пустого списка у экрана нет: он сам и есть пустое состояние; ошибку отправки показывает существующее эхо с Retry (web-ui-spec.md:63).

## экран 3. строка у поля ввода: модель, рассуждения, роль
Снимки: `menu-*.png`, `chips-busy.png`, `model-switch-*.png`.

Место. Нижний ряд поля ввода (`.composer-foot`) слева направо: скрепка, кнопка «/», три чипа, справа отправка. Кнопка `#model-selector` справа убирается: её роль берёт чип модели. Строка состояния под полем (web-ui-spec.md:95-101) не меняется.

Чип: кнопка 28px, 12px `--sans`. На ширине больше 640px «Model kr/claude-sonnet-4.5», «Reasoning medium», «Role auto», где имя `--text-3`, значение `--text`, затем шеврон 12px. До 640px только значения, имя остаётся в `aria-label` («Reasoning: medium») и в заголовке меню. Модель режется многоточием (до 180px, на 390 до 96px), чипы рассуждений и роли не сжимаются. Значения как в ядре и TUI: `auto`, `medium`, `off` вместо `false`.

Меню: `role="menu"`, пункты `menuitemradio` с `aria-checked`, открывается вверх над полем ввода, левый край у чипа, ширина до 420px (на 390 во всю ширину поля), высота до 420px с прокруткой. Фон `--bg-1`, рамка `--line`, без тени. Первая строка меню 12px `--text-3` говорит, что меняет выбор. У каждого пункта две строки: название 13px и подпись в одну строку 12px `--text-3`; галочка `--accent` у текущего (accent-color выбранного состояния разрешён, web-ui-spec.md:15). До 640px подпись переносится на вторую строку, а не обрезается: описания ядра до 62 символов.

| меню | заголовок | пункты и подписи | действие |
|---|---|---|---|
| модель | «<Provider> models. Applies to this session and new ones.» | модели каталога; подпись: описание модели из `options` ядра, если есть (kernel/llm_clients.py:459-620), иначе «From your <Provider> catalog», у текущей «Current model»; разделитель; «Enter a model ID…» / «Use a model that is not in this list»; «Add a provider…» / «Connect another service with its own key» | `models [X, smart]` |
| рассуждения | «How much the model thinks before it acts. Applies now and to new sessions.» | шесть уровней с описаниями `EFFORT_DESCRIPTIONS` | `effort [L]` |
| роль | «What Kvist may do in your workspace. Applies now and to new sessions.» | шесть ролей с описаниями `ROLE_DESCRIPTIONS` и `auto` (core_manifest.py:49-66) | `role [R]` |

Каталог моделей: при первом открытии меню за сессию запрос `POST /api/v1/configuration/models` с сохранённым ключом (как TUI, kernel/main.py:2158-2177); при фильтре больше 8 моделей вверху поле «Filter models». Smart: если smart совпадал с прежней fast, он следует за новой моделью, иначе остаётся; раздельный выбор ролей живёт в окне провайдера.

Куда уходит выбор:
- открытая сессия с ядром: `POST /api/v1/command` (`models`, `effort`, `role`). Ядро меняет сессию и само сохраняет выбор как умолчание (kernel/main.py:3066, 3098, 3275), поэтому один выбор делает обе вещи, и заголовок меню говорит об этом прямо. Режим «только для этой сессии» (Claude Code, `s` в `/model`) не делается: для него нужна новая команда ядра.
- нет сессии или её ядро не запущено: `POST /api/v1/configuration` с текущими provider и models и новым effort или role (поле `role` новое, сохраняется `user_config.save_role_selection`).

Состояния:
- загрузка каталога: строка с кольцом «Loading models…» вместо списка, пункты «Enter a model ID…» и «Add a provider…» доступны (`menu-model-loading.png`).
- ошибка каталога: строка `--err` «Couldn't load the model list from <Provider>. Try again» с кнопкой, ниже текущая модель (`menu-model-error.png`).
- пустой каталог: «<Provider> returned no models.» и «Enter a model ID…» (`menu-model-empty.png`).
- длинный каталог: поле фильтра, фокус в нём (`menu-model-long.png`).
- применение: значение в чипе меняется сразу, перед ним кольцо 10px, пока не пришёл `command_result` (`model-switch-pending.png`). `ok: true`: кольцо уходит, тост «Model changed to X for this session and new ones.» (`model-switched.png`). `ok: false`: значение возвращается, тост с текстом ядра, например «Wait for the current task to finish before changing models.» (`model-switch-failed.png`).
- агент занят (running, starting, waiting_for_user): чипы `aria-disabled="true"`, opacity .6, title «Available when Kvist finishes the current task»; нажатие показывает тост «Kvist is working. Change this when the task finishes.» (`chips-busy.png`). Контроллер такие команды и так отклоняет (processes.py:137-139).

Обязательная правка вместе с экраном. Сейчас `command_result` не доходит до интерфейса (аудит, задача 7): контроллер публикует его как durable (processes.py:25-34), журнал его не пишет, клиент durable-событие живьём не применяет (blocks.js:38-39). Правка: `command_result` в `_TRANSIENT_CORE_EVENTS`, потому что флаг durable означает «будет в журнале», а журнал его не хранит. Мок redesign.mjs:147 шлёт `command_result` без флага durable, поэтому сценарий проходил; новый сценарий проверяет путь через настоящий контроллер.

Клавиатура. Чипы в Tab-порядке после «/». Enter, Space и стрелка вверх или вниз на чипе открывают меню, фокус на текущем пункте; стрелки, Home, End двигают; Enter и Space выбирают и закрывают, фокус возвращается на чип; Escape закрывает с возвратом фокуса; Tab закрывает и идёт дальше; клик мимо меню закрывает без переноса фокуса. Проверено `prototype/keyboard.mjs`.

## экран 4. команды
Снимки: `slash-menu.png`, `slash-filtered.png`, `slash-no-match.png`, `palette.png`.

Меню «/»:
- открывается, когда поле начинается с «/» и до первого пробела (`/^\/\S*$/`); «/» внутри текста и путь `see /tmp/notes` меню не открывают (так в Claude Code, аудит, паттерн 2). Поле получает `role="combobox"`, `aria-expanded`, `aria-activedescendant`, меню `role="listbox"` во всю ширину поля над ним.
- строки: имя команды `--mono` 12.5px и описание `--sans` одной строкой. Набор (только то, что веб умеет сделать):

| команда | описание | действие |
|---|---|---|
| /model | Change the model for this session and new ones | открывает меню чипа модели |
| /reasoning (и /effort) | Change how much the model thinks before it acts | меню чипа рассуждений |
| /role | Change what Kvist may do in your workspace | меню чипа роли |
| /new | Start a new session; this one stays in the sidebar | New session |
| /provider | Add a provider or switch to another one | окно провайдера (экран 5) |
| /diff, /files | Show the changes Kvist made in this session; Show the files Kvist read and changed | вкладки Diff, Files |
| /stop | Stop the current task | Stop |

- фильтр по началу имени после «/»; стрелки двигают, Enter выполняет (Ctrl+Enter по-прежнему отправляет текст как сообщение), Tab дописывает имя, Escape закрывает меню и оставляет текст. Нет совпадений: «No command starts with /xyz. Press Ctrl+Enter to send it as a message.».
- подвал меню: «↑ ↓ to move, Enter to run, Esc to close» и кнопка «All commands, Ctrl+K», открывающая палитру.
- видимая кнопка «/» в поле ввода (28px, `aria-label="Commands"`, title «Commands (/)»): ставит «/» в пустое поле и открывает меню; в непустом поле открывает меню без изменения текста, если оно начинается с «/», иначе открывает палитру.
- палитра (palette.js:5) получает «Change model», «Change reasoning», «Change role», «Add a provider» с подписью-командой справа; Switch server и Connect server скрываются вместе с селектором серверов, пока `SHOW_SERVERS = false`. Кнопка Commands в подвале сайдбара остаётся.
- Settings в сайдбаре: пока удалённый режим заморожен, пункт открывает окно провайдера, а не синхронизацию с несуществующим селектором Local (connections.js:201-210).
- состояния загрузки и ошибки у меню нет: набор команд статический в клиенте, данные нужны только для подменю.

## экран 5. добавление провайдера и смена модели посреди сессии
Снимки: `add-provider.png`, `add-provider-saved.png`, `model-switch-*.png`.

- «Add a provider…» в меню модели, `/provider` и палитра открывают окно «Add a provider»: та же форма, что второй путь экрана 1 (Provider, ключ, Model, Provider address), кнопки Cancel и «Save provider». Сохранение это `POST /api/v1/configuration` с проверкой ключа.
- после сохранения окно говорит, что будет с открытой сессией: «<New> is connected. This session keeps <Old> with <model>. New sessions use <New> with <model>.» и две кнопки: «Keep this session» и «Start a session with <New>» (`add-provider-saved.png`). Почему не переключать сразу: смена провайдера пересобирает агента (kernel/main.py:3236-3264 для моделей), веб пропускает только `models`, `effort` и `role`, а молча оставить сессию на старом провайдере после «Save» значит обмануть ожидание.
- смена модели того же провайдера посреди сессии: меню чипа, пункт, подтверждение (экран 3, «применение»). Тост встаёт над полем ввода, а не на него (сейчас на 390 он закрывает отправку, аудит, задача 2): `.toasts` с отступом снизу на высоту области ввода на всех ширинах.
- окно «Model & provider» (configuration.js) остаётся для ключей и раздельных моделей fast и smart; режим рассуждений и кнопки «Apply reasoning», «Use model in this session» из него уходят в чипы.
- клавиатура: окно ловит фокус (aria-modal), фокус на первом поле, Escape и Cancel закрывают и возвращают фокус на элемент, открывший окно.

## ключи
- поле ключа: `type="password"`, `autocomplete="off"`, `spellcheck="false"`, без атрибута `name`: при отказе JavaScript форма не сериализует ключ в адрес (csp разрешает form-action 'self', и запасная отправка формы GET-запросом унесла бы ключ в URL). Форма без `action`, отправка только через `fetch` POST.
- ключ не попадает в адрес страницы и в адрес любого запроса, в `localStorage`, `sessionStorage`, IndexedDB, cookie, в консоль, в журнал сессии и логи контроллера, в ответы API (сейчас `configuration_info` отдаёт только `present`, configuration.py:27-30) и в тексты ошибок (configuration.py:91-94 уже не возвращает тело ответа провайдера).
- после успешного сохранения значение поля стирается, а экран первого запуска удаляется из DOM целиком; в окне провайдера поле стирается (как сейчас, configuration.js:115) и окно закрывается. После неверного ключа значение остаётся в поле, пока человек его правит.
- ключ уходит только на свой сервер (`/api/v1/configuration`, `/api/v1/configuration/models`), сервер проверяет его у провайдера и сохраняет существующими писателями ядра (configuration.py:129-143).

Тест на утечку (новый сценарий `tests/browser/onboarding.mjs` на фикстуре `setup_server.py`, временный HOME): уникальный ключ `qa-leak-<случайное>`, принятый фикстурой; пути: неверный ключ, верный ключ и Start, перезагрузка, окно «Add a provider», смена модели. Проверки: ключа нет в `page.url()` и в URL всех запросов (`page.on('request')`), он есть только в телах `POST /api/v1/configuration` и `/configuration/models`; нет в `localStorage`, `sessionStorage`, именах и записях IndexedDB, `context.cookies()`; нет в `document.documentElement.outerHTML` и значениях всех полей после Start; нет в сообщениях консоли и данных SSE; нет в ответе `GET /api/v1/configuration`, в `web.jsonl` контроллера и в `.events.jsonl` сессии во временном HOME. Прототип уже проверяет хранилища, адрес и DOM (`prototype/keyboard.mjs`, последний шаг).

## данные и API
- `GET /api/v1/configuration` (configuration.py:20-46) добавляет `roles` и `effort_levels_info` из `build_manifest()` (`value` и `description`) и `role` (`get_saved_role() or "auto"`). Старые поля не меняются, старый клиент их игнорирует.
- `POST /api/v1/configuration` принимает необязательное `role` из `_ROLE_CHOICES`.
- `POST /api/v1/configuration/models` при ошибке отдаёт `error_kind` (`auth`, `unavailable`, `other`) рядом с прежним текстом.
- `POST /api/v1/command` (processes.py:134-136) пропускает `role` с одним аргументом из `_ROLE_CHOICES`.
- `command_result` в `_TRANSIENT_CORE_EVENTS` (processes.py:25-34).
- событие `manifest`, которое клиент сейчас отбрасывает (blocks.js:41), не нужно: те же данные приходят с конфигурацией до запуска ядра, а меню нужны и без сессии.
- для удалённого сервера те же поля приходят через мост; старый мост без `web_configuration` получает экран «This server runs an older Kvist…» (как сейчас окно провайдера, frontends/web-local/README.md:187-189).

## бюджет размера
Сейчас 201 476 байт из 204 800 (после 1c400e8), запас 3 324. Правило (web-ui-spec.md:157) суммирует все .js, .css и .html в client/ без fonts/.

Оценка реализации по разделам прототипа (proto.js 31 292 байта с симуляцией, proto.css 8 475) за вычетом симуляции и с переиспользованием формы и каталога из configuration.js:

| часть | байт |
|---|---|
| экран 1: разметка, проверка ключа, оба пути, состояния | +7 000 |
| экран 2: пустая сессия и стартовые действия | +1 300 |
| экран 3: чипы, общий компонент меню, три меню, применение и откат, правка `command_result` | +6 500 |
| экран 4: меню «/», кнопка «/», пункты палитры | +3 800 |
| экран 5: окно провайдера на общей форме, выбор сессии | +2 500 |
| css новых частей | +6 500 |
| убрать: рассуждения и кнопки сессии из окна модели, `#model-selector` с его css | −2 100 |
| итого | ≈ +25 500, клиент ≈ 227 000 байт |

Пути:
1. отдельный модуль онбординга с ленивой загрузкой (`import()` при `setup_required`). По текущему правилу размер не меняется: правило считает все файлы client/, а не загружаемые сразу, итог те же ≈ 227 000. Даже при новом правиле «только загружаемое сразу» вне счёта окажутся экран 1 и окно провайдера (≈ 7 000 + 12 549 configuration.js), сразу загружается ≈ 207 500, это всё равно больше 204 800. Цена: состояние загрузки модуля и второй путь ошибок.
2. сокращение существующего кода. Мёртвого css почти нет: из 141 класса в styles.css в js и html не найдено 5, четыре из них собираются строкой (`ind-${…}`), правила этих классов ≈ 800 байт. Крупный кандидат один: замороженный удалённый режим (connections.js 17 345 байт, из них диалоги серверов ≈ 12 000), но это удаление работающей функции, о котором решает владелец, а README обещает удалённые агенты. Без него сокращение даёт < 1 000 байт.
3. новый предел 240 КиБ (245 760 байт). Запас после реализации ≈ 18 700 байт (≈ 8%).

Выбор: путь 3. Он единственный покрывает оценку без удаления функций и без новой механики загрузки, и он совпадает с обоснованием самого предела: «функции важнее килобайт на localhost… предел только ловит случайный рост» (web-ui-spec.md:158). Предел в этой фазе не менялся, меняет его владелец правкой web-ui-spec.md:157.

## план тестов
- `tests/browser/onboarding.mjs` (новый, фикстура `setup_server.py`): путь новичка за 4 действия на 1440x900 и 390x844 со снимками; все состояния экрана 1 (неверный ключ, недоступность, пустой каталог, заблокированная вкладка через `context.on('page')` с закрытием и подменой `window.open`); второй путь с DeepSeek; тест на утечку ключа (раздел «ключи»).
- `tests/browser/status-states.mjs` дополняется чипами: значения, ширины 1440, 1280, 390 без горизонтальной прокрутки, Tab-порядок, кольцо фокуса, меню и клавиатура, состояние занятости. Проверка `#model-selector` в нём меняется на чип модели (сценарий описывает поведение, которое уходит).
- сценарий смены модели через настоящий контроллер и ядро на фальшивом провайдере (стенд `web-onboarding/audit/stand.py`): чип показывает новую модель после `command_result`.
- `tests/client/format.test.mjs`: разбор «/» (что открывает меню, что нет), фильтр команд, выбор начальной модели по порядку «сохранённая, defaults.fast, пусто».
- пороги прежние: нет ошибок страницы, нет горизонтальной прокрутки, motion-states проверяет, что меню, чипы и экран 1 анимируют только opacity и transform.

## ручные проверки в Firefox
Firefox для Playwright здесь не установлен; эти пункты проходятся руками в Firefox последней версии.
1. Вставка ключа Ctrl+V в поле пароля запускает проверку без Enter (событие paste), после ошибки рамка поля красная.
2. При «Block pop-up windows» и запрете для localhost кнопка регистрации показывает строку о заблокированной вкладке со ссылкой, а ссылка открывает anymodel.org.
3. После Start Firefox не предлагает сохранить ключ как пароль; после перезагрузки и после «Restore previous session» поле ключа пустое.
4. Список моделей на 200+ пунктов (нативный select): поиск по первым буквам работает, список не выходит за окно на 390 в Responsive Design Mode.
5. Меню чипа: открывается над полем, Escape закрывает и возвращает фокус на чип, кольцо фокуса видно с клавиатуры и не видно после клика мышью.
6. «/» в русской раскладке набирается другой клавишей, чем в английской: меню открывается от введённого символа «/», а не от кода клавиши; при вводе через IME меню не мигает.
7. `ui.prefersReducedMotion = 1` в about:config: кольца в чипах, проверке ключа и меню не вращаются, меню появляются сразу.
8. oklch, `color-mix` и `:has()` (строка состояния) рисуются как в Chromium: сравнить с `shots/1440/menu-effort.png`.
9. На 390 тост встаёт над полем ввода и не закрывает чипы и отправку; горизонтальной прокрутки нет.
10. Шторка сайдбара на 390 закрывается после выбора команды палитры (аудит, задача 5) и по тапу в затемнение.

## открытые вопросы
1. Модель AnyModel по умолчанию. У ядра её нет (`_MODEL_DEFAULTS` без AnyModel, kernel/llm_clients.py:427; TUI спрашивает модель отдельным шагом, kernel/main.py:2952-2990). Предложение: один id в `_MODEL_DEFAULTS[anymodel]`, выбор id за владельцем; тогда экран 1 даёт 2 действия, без него 4, а весь путь 6. Прототип ставит первую модель каталога на место этой модели по умолчанию.
2. Подписи моделей из живого каталога: у ядра нет описаний для id, которых нет в `_MODEL_OPTIONS`; подпись «From your <Provider> catalog» говорит, откуда модель, а не что она меняет. Что она меняет, говорит заголовок меню.
3. Удалённый режим: правка Settings (экран 4) и скрытие Switch server в палитре следуют за `SHOW_SERVERS`; при его включении оба пункта возвращаются.
4. Запуск: в корневые README.md и README.ru.md раздел о `kvist-web`, в эпилог `kvist --help` одна строка о нём; предупреждение на экране 1, когда воркспейс это домашний каталог («Kvist will work in your home folder. Add a project folder instead?»). Это вне клиента и вне этой фазы.

## прототип
- `prototype/app.html`, `proto.js`, `proto.css`: подключает настоящий styles.css клиента по относительному пути и добавляет только новые части. Без сервера, без сети, без хранилищ; каталог и проверка ключа симулированы (ключ `qa-valid-key` работает, `qa-empty-catalog-key` с пустым каталогом, `qa-offline` без сети, остальные отклоняются).
- `prototype/fixture.js` генерирует `make_fixture.py` из ядра на пустом временном HOME: адрес регистрации, роли, уровни, провайдеры и их встроенные модели. Своих значений в прототипе нет, кроме образца каталога AnyModel (два id из примеров ядра, kernel/main.py:2789, и четыре id из встроенных списков `_MODEL_OPTIONS`) и образца живого каталога DeepSeek (его два встроенных id).
- `prototype/index.html` перечисляет 32 состояния, `states.js` их список.
- `prototype/shoot.mjs` снимает каждое состояние на 1440x900 и 390x844 под CSP клиента и падает на нарушении CSP, ошибке страницы или горизонтальной прокрутке; `prototype/keyboard.mjs` проходит экраны 1, 3 и 4 с клавиатуры и проверяет, что ключа нет в хранилищах, адресе и DOM.
