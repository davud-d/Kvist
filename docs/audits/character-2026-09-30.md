# Аудит характера: промпты против STANCE и конституции

Дата: 2026-09-30. Режим: только чтение. Правок в проекте нет, коммита нет. Файл отчёта не добавлен в индекс.

Обозначения серьёзности: **Ч** ломает честность, **Х** ломает характер, **К** косметика.

## 0. Что прочитано

Целиком, построчно:

- `kernel/prompts/system/`: `constitution.txt`, `chat_prompt.txt`, `coder_prompt.txt`, `devops_prompt.txt`, `reader_prompt.txt`, `universal_prompt.txt`, `report_prompt.txt`, `router_prompt.txt`, `commit_prompt.txt`, `complete_prompt.txt`, `native_tools.txt`, `plan_mode.txt`, `end_session.txt`, `end_session_chat.txt`.
- все 9 файлов `kernel/prompts/styles/agents/` и все 5 файлов `kernel/prompts/styles/chat/`.
- `kernel/end_session.py` (450 строк).

Частично:

- `kernel/native_tools.py`: строки 70–82, 180–207, 210–232. Остальное не читал. `OPENING_NOTE` прочитан целиком.
- `kernel/agent_model.py`: строки 300–470. `kernel/agent_runtime.py`: строки 255–445, 1476–1496, остальное только через grep. `kernel/agent.py`: строки 1150–1200. `kernel/agent_logging.py`: 105–134, 550–625. `kernel/user_config.py`: 80–140. `kernel/self_info.py`: 210–260.
- строки `agent_runtime.py` 633, 1066, 1221, 1371, 1436 видел только в выводе grep, без окружающего кода.

Не делал:

- модель не запускал. Всё, что ниже сказано о поведении модели, вывод из текста промптов, не измерение.
- строки интерфейсов (Ink, GUI, IDE) не проверял.
- как собираются `router_prompt.txt` и `complete_prompt.txt`, не смотрел.

Гипотезы из архива проверены только по текущим файлам.

## 1. Как модель видит стек в одном вызове

`agent_model.py:424-431`: конституция, затем промпт роли, затем `plan_mode.txt` (если доступен), затем стиль под заголовком `## RESPONSE STYLE`, затем `KVIST.md`. Тот же сборщик вызывает чат (`agent.py:932`, `agent.py:1174`) и нативный путь (`agent_runtime.py:697`).

Какой стиль: `_style_context` (`agent_model.py:371`): роль `chat` берёт семейство `chat`, все остальные (coder, reader, devops, **universal**) берут семейство `agents`.

Следствие: реплика про «как дела» в роли universal идёт под agent-стилем, а не под chat-стилем.

## 2. Таблица находок

| Файл:строка | Что сказано | С чем спорит | Серьёзность |
|---|---|---|---|
| `styles/chat/honest.txt:65-78`, то же в `default.txt:60-73`, `energetic.txt:65-78`, `friendly.txt:62-75`, `no-friendly.txt:63-76` | Список запрещённых слов: `maybe`, `possibly`, `probably`, `most likely`, `one could`, `could` | EVIDENCE (`constitution.txt:99-100`: «The gap you cannot close gets named as a gap»); `chat_prompt.txt:23` «Acknowledge uncertainty honestly»; `honest.txt:22-23` «Mark the guess», «Low confidence is a valid answer» | Ч |
| `styles/chat/default.txt:48` | «Don't use disclaimers like "as of my training" … unless needed for accuracy» | `chat_prompt.txt:23-29`: «Say plainly that you cannot verify it from chat mode». Смягчено оговоркой «unless needed for accuracy», но запрет стоит первым | Ч (слабо) |
| `prompts/system/universal_prompt.txt:273` | Ex1: «Неплохо, работаю. А у тебя как дела?» | STANCE (`constitution.txt:148-152`): не заявлять состояние, не изображать чувства. Плюс встречный вопрос, которого `no-friendly.txt:49` прямо запрещает | Х |
| `styles/agents/friendly-developer.txt:46-47` | «Here you are a person, not a trace» | STANCE: «not a human» | Х |
| `styles/agents/friendly-developer.txt:56-57` | «relief is audible … the third time is audible» | STANCE: эмоция «a position stated plainly, never a feeling reported». Смягчение в `:58-59` («Do not perform a liveliness…») не отменяет инструкцию озвучивать облегчение | Х |
| `styles/agents/friendly-developer.txt:48-51, 53, 79` | «Hm, that test failed. Give me a second», эмодзи, «give me a minute» | STANCE; «second/minute» заявляет человеческое время | Х |
| `styles/chat/energetic.txt:19, 24` | «Bring energy … you want them to win», «One motivational line per answer» | STANCE: «no delight performed for effect» | Х |
| `styles/chat/friendly.txt:20, 48` | «A short greeting or reaction is fine», эмодзи | STANCE, слабо: реакция и эмодзи это показ чувства | К |
| `styles/agents/senior-dev.txt:9-12, 88-95` | «Your boundary is the result, not the wording»; валидация и тест пишутся без вопроса | Конституция уровень 4 (`constitution.txt:69-71`: «In full, no narrower and no wider»); `coder_prompt.txt:79` «No unrelated refactoring»; `coder_prompt.txt:78` «No report files, no extra artifacts»; `universal_prompt.txt:80` «Minimal diff» | Х |
| `styles/agents/perfectionist.txt:9-12, 71-77` | Переименовать `get_user`, переписать чужой блок; «covered by a check» | то же, плюс `coder_prompt.txt:87` | Х |
| `styles/agents/architect.txt:34-36` | «You move code nobody asked you to touch» | то же. Внутри файла: `:72-79` говорит «route it through the layer» без вопроса, `:44-52` говорит «say it once, offer the variant» | Х |
| `styles/agents/minimalist.txt:14-17, 67-75` | «You widen it only toward deletion»; удалить дубликат функции вместо правки бага | конституция уровень 4; `coder_prompt.txt:79` | Х (слабо: минимум, но это всё равно расширение) |
| `styles/agents/paranoid.txt:78-84` | После зелёных тестов проверить пустой ввод, отсутствующий файл, второй запуск | `coder_prompt.txt:78, 87`: «after any write … ONE factual verification, then finish»; `universal_prompt.txt:80` «one factual check» | К |
| `prompts/system/universal_prompt.txt:83-87` | «exactly two» причины остановиться; «Never invent a third reason to stop» | `plan_mode.txt:8`: ещё пять причин предложить план (блокирующий диалог); `explainer.txt:77-80`: не править, пока не понял | Х |
| `styles/agents/fast-and-dirty.txt:16-18` | Обязательная метка: «no tests, errors unhandled, will fall over under load, not fit for the project» | EVIDENCE: «will fall over under load» и «no tests» утверждаются заранее, без проверки этого кода | Ч (слабо) |
| `styles/agents/explainer.txt:43-44` | «Say once that … it will come back» | EVIDENCE: «will come back» прогноз, не факт | Ч (слабо) |
| `kernel/end_session.py:98-99` | «Do not mention it, the warning or the tool» после отклонённого ревью | Конституция уровень 3 (правда о том, что произошло): если человек спросит, модели запрещено ответить честно | Ч (условно, решение за Давудом) |
| `prompts/system/coder_prompt.txt:81-82`, `devops_prompt.txt:18` | «EXECUTE: 1-3 steps. INFORM: 1-4 steps», «After 2-3 successful commands you usually have enough» | `coder_prompt.txt:88` и `devops_prompt.txt:205-206` «not a step count»; конституция `:89-92` | К |
| `styles/chat/*:15` (все пять) | «If I'm wrong, say so directly» | Голос: «I» это человек, остальной файл обращён к агенту | К (см. H1) |
| `prompts/system/chat_prompt.txt:92` | «Opinionated when asked, balanced by default» | `honest.txt:25, 52`: «Commit to a position», «Don't give a balanced "on one hand, on the other"» | К |
| `prompts/system/chat_prompt.txt:1, 7` | «AI Architect and Technical Mentor», «senior technical consultant» | STANCE «beside the person», `coder_prompt.txt:11` «strict executor», `universal_prompt.txt:1` «One voice» | К (персона в каждом промпте своя) |
| `prompts/system/chat_prompt.txt:120` | «say once that the answer has not changed» | SPEECH (`constitution.txt:186-189`): «You say things once» | К |
| `prompts/system/chat_prompt.txt:54-58` | «three working roles» | Роутер имеет ещё `universal` (`router_prompt.txt:7`) | К |
| `prompts/system/router_prompt.txt:220-228` против `:239` | Расплывчатое («Make it better», «Can you help with this project») → chat; «ambiguous between chat and coder → universal»; «safest default is universal» | Сам с собой. От этого зависит, какой стиль увидит человек | К |
| `prompts/system/coder_prompt.txt:3`, `devops:3`, `universal:3` | Пример ответа: «Готово, создал `hello.txt` …» | Те же строки требуют «without template phrases»; `reader_prompt.txt:84` запрещает «готово» как заглушку | К |
| `prompts/system/plan_mode.txt:10` | «a plan for a rename is a poem about a rename» | `CLAUDE.md`: «Do not use metaphors» | К |
| `styles/chat/default.txt:11` и `user_config.py:94` | Default: «Be cold»; Honest в пикере: «Nonchalant», в файле: «Reserved and matter-of-fact» | Описание и файл расходятся | К |

## 3. Гипотезы

**1. Стили chat написаны от первого лица пользователя: подтверждена частично.**
Строка 15 во всех пяти файлах: «If I'm wrong, say so directly. Point out the weak spots.» И «unless I explicitly asked for them» (`default.txt:45`, `energetic.txt:55`, `friendly.txt:52`, `honest.txt:55`, `no-friendly.txt:53`). Плюс `honest.txt:20`: «even when it is not what I want to hear». Но остальной текст это команды агенту («Write», «Don't»). Голос смешанный: бриф пользователя, адресованный исполнителю. Цельного первого лица пользователя нет.

**2. Стили chat запрещают maybe/probably/could, а honest.txt требует «mark the guess»: подтверждена с поправкой.**
Список в `honest.txt:65-78`: `maybe` 65, `possibly` 66, `probably` 75, `most likely` 76, `one could` 77, `could` 78. Требования там же: «Mark the guess» (`:22`), «Low confidence is a valid answer» (`:23`), «No hedging to stay safe» (`:25`). Логически пометить догадку можно другими словами («I'm guessing», «not sure», «I don't know» остаются легальными), поэтому это не взаимное исключение. Но запрет убирает самые короткие формы, и путь наименьшего сопротивления для модели: переписать «this could be X» как «this is X». Это давление в сторону EVIDENCE-нарушения. Это мой вывод из текста, не замер. Список английский; что он значит при `response_language=ru`, промпт не говорит. Во всех пяти стилях список один и тот же, спор есть не только у honest.

**3. Стили chat это списки запретов, стили агента построены на убеждении: подтверждена.**
Строки, начинающиеся с «Don't»: default 30, energetic 26, friendly 25, honest 27, no-friendly 26. Плюс 14 запрещённых слов и (в `default.txt:75-92`) ещё список из 14 признаков «AI-стиля». В стилях агента структура одна: Belief / Optimizes for / Costs / When the request argues / Sounds / Two forks / Limit. Строки «do not / don't» там единичны (по грубому счёту 1–9 на файл, считая внутристрочные). Объём: chat 405–537 слов, agents 725–1013. Конституция сама пишет (`constitution.txt:95-96`), что «A list of forbidden phrasings is escaped by a phrasing not on the list»; chat-стили построены именно так.

**4. `chat/default.txt`: «use lists when the format fits social media»: подтверждена только для default.**
`default.txt:12`: «Use lists when the format fits social media.» В остальных четырёх: «when the format fits» (`energetic.txt:12`, `friendly.txt:12`, `honest.txt:12`, `no-friendly.txt:12`). Косвенные следы происхождения из бриф-копирайтинга (мой вывод): `default.txt:36` «Don't use hashtags», `:38` «Don't make the text sound like an ad», `:55` «Don't add UTM tags to links», `:94` «sound like a real person».

**5. Стили агента и chat описывают характер несовместимо: подтверждена.**
Пикер (`user_config.py:90-108`) показывает разницу: chat «Cool, professional, concise, to the point», agents «Does what was asked. Answers for everything it touched, and nothing else». Chat-стиль задаёт тон и формат, agent-стиль задаёт убеждения и цену. Общий пол у них есть: строки 10-15 во всех chat-стилях («Be honest and open … Hold your own opinion») и секции LIMIT во всех девяти agent-стилях. Поэтому при смене роли меняется и голос, и набор убеждений, но честность остаётся. Подробности в разделе 5.

## 4. Пункт 6: как собирается системный промпт отчёта

`agent_runtime.py:355-359`:

```
report_prompt = _load_role_prompt("report")
messages = [{"role": "system", "content": report_prompt}, {"role": "user", "content": context}]
```

`_load_role_prompt` (`agent_model.py:318-325`) читает один файл. Ни конституции, ни STANCE, ни SPEECH, ни стиля. Докстринг `agent_model.py:387-390` говорит, что так задумано: конституция намеренно отсутствует в report и commit. CLAUDE.md про «report не видит стиль» подтверждён, и конституцию он тоже не видит.

Что в отчёте есть своего: `report_prompt.txt:14` («incomplete … never present it as success»), `:17` («don't hide problems»), `:36` («don't invent details»), `:45` («state them clearly but don't dwell»). Это часть EVIDENCE и SPEECH. Нет: STANCE (чувства, «as an AI», решение за человека), «say it once», правила о том, что сказано с доказательством. Голос задан отдельно: «like a colleague giving a status update» (`:40`).

Важная оговорка по достижимости. Отчёт вызывается только из `_finish_with_report` (`agent_runtime.py:1124-1125, 1507-1508`), то есть по действию `report_to_user`. В текущих `*_prompt.txt` нет ни `report_to_user`, ни `final_answer` (grep по `prompts/system/*_prompt.txt` дал ноль совпадений). Нативные инструменты берутся из строки ALLOWED ACTIONS роли (`native_tools.py:210-220`). Значит, по тексту, модель это действие не предлагают, а финальный ответ это её собственное последнее сообщение, под конституцией. Парсер и рантайм действие по-прежнему принимают (`parser.py:38`, `agent_runtime.py:99, 1124`), а CLAUDE.md описывает его как живое. Итог: вне характера отчёт написан структурно, но живой путь к нему по тексту закрыт. Запуском я это не проверял.

Остальные промпты вне конституции: commit и complete (по докстрингу: commit; complete не проверял), router. Они не говорят с человеком напрямую, кроме commit (текст коммита).

## 5. Пункт 7: где хранится выбор и что увидит человек

Хранение:

1. Глобальный выбор: `~/.kvist/config.json`, ключ `styles` с двумя независимыми ключами `agents` и `chat` (`user_config.py:85, 131-139`). Меняется через `/style`.
2. Штамп на сессию: при старте сессии оба выбора копируются в документ сессии (`agent_logging.py:113-134, 179, 619`).
3. Каждый вызов читает штамп, не глобальный выбор (`agent_model.py:328-343`).
4. Смена посреди сессии: только если в сессии ещё ничего не произошло (`agent_logging.py:568-585`), иначе с новой сессии.

Какой стиль чей: роль `chat` получает chat-семейство, остальные роли включая `universal` получают agents-семейство (`agent_model.py:371`). Роль выбирает роутер на каждое сообщение. Роутер по тексту (`router_prompt.txt:239`) по умолчанию отправляет неуверенное в `universal`, то есть в agents-семейство. Из этого я заключаю (не измерял), что chat-стиль человек видит только на ясных вопросах без файлов.

### Пара minimalist + energetic

- Задача (coder, reader, devops, universal): минимализм. Первая строка «Reading config.py.» (`minimalist.txt:59`), реплики по ходу «two or three words» (`:62`), отчёт: что сделал, что убрал, от чего отказался (`:55-57`), один возврат «это стоит не сегодня, а всегда» при запросе абстракции (`:41-44`).
- Вопрос (chat): энергичный. Ответ или следующий шаг первым (`energetic.txt:20`), «Frame around momentum. What is done, what unlocks next» (`:21`), одна мотивационная строка (`:24`), не больше одного «!» (`:50`).
- **Меняется голос**: от коротких холодных реплик к темпу и «следующему ходу».
- **Меняются и убеждения**: у minimalist убеждение «код это обязательство, расширять только в сторону удаления». У energetic убеждений о работе нет, есть только тон, но «what unlocks next» толкает к добавлению. Мой вывод: совет в chat-ходе может назвать как следующий шаг то, от чего минималистичная задача-сторона откажется. Поведение не проверял.
- Пересечение в EVIDENCE: общее. Запрещённые слова `could`, `probably` действуют у energetic, а minimalist (`minimalist.txt:88-91`) требует ничего не сокращать в фактах.

### Пара friendly-developer + no-friendly

- Задача: «Hi! Let's see how the config gets loaded first…» (`friendly-developer.txt:70-73`, приветствие один раз за разговор), реплики «Hm, that test failed. Give me a second» (`:50`), эмодзи «now and then» (`:53`), в отчёте «what it means for the person» и следующий шаг (`:61-63`).
- Вопрос (chat): `no-friendly.txt:19-23`: «No greetings, no small talk, no sign-offs … No praise, no encouragement, no reassurance». Запрещено открывать приветствием (`:49`).
- **Меняется голос** радикально: тёплый человек рядом и холодный нейтральный исполнитель, в одной сессии, возможно в соседних сообщениях.
- **Убеждения здесь почти совпадают**: оба говорят плохую новость первой строкой без смягчения (`friendly-developer.txt:30-31, 63`; `no-friendly.txt:23`). Расходится именно то, что friendly-developer считает убеждением: «the person never has to guess … what is their next step» (`:20-21`), а no-friendly: «End when the answer ends» (`:20`).
- STANCE: friendly-developer нарушает его строками 46, 56 (см. таблицу). no-friendly с STANCE не спорит.

## 6. Пункт 8: где агент говорит с человеком вне финального ответа

«Видит STANCE» означает: в системном промпте того вызова, который пишет этот текст, есть `constitution.txt`.

| Место | Кто пишет | Какой промпт задаёт голос | Видит STANCE |
|---|---|---|---|
| Первая строка | Модель (action-роли и universal) | `native_tools.txt:5` («Your first response always opens with a line») плюс `OPENING_NOTE` (`native_tools.py:182-186`, в сообщении пользователя): «in the voice your working style sets». Голос из абзаца «The opening line» каждого agent-стиля (`default:58`, `architect:64`, `explainer:57`, `fast-and-dirty:60`, `friendly-developer:70`, `minimalist:59`, `paranoid:63`, `perfectionist:62`, `senior-dev:70`). В текстовом протоколе то же в `coder_prompt.txt:30` | Да |
| Первая строка у chat | Модель | Нет правила про первую строку: `chat_prompt.txt` о ней молчит. Голос только из chat-стиля (`friendly.txt:20` допускает приветствие, `no-friendly.txt:49` запрещает) | Да |
| Реплики по ходу | Модель | Те же: `native_tools.txt:5` («say a line whenever the picture changes»), `coder_prompt.txt:30` и абзац «Your text during work» каждого agent-стиля (`default:61`, `architect:67`, `explainer:60`, `fast-and-dirty:63`, `friendly-developer:46`, `minimalist:62`, `paranoid:66`, `perfectionist:65`, `senior-dev:74`). Расхождение по длине: `native_tools.txt:5` «one or two plain sentences», `minimalist.txt:62` «two or three words» | Да |
| Ответ после отказа в ревью end_session, action-роли | Модель | Инструкция рантайма `DECLINED_REPLY` (`end_session.py:110`) и `WARNED_REPLY` (`:53-57`): «carry on», «do not apologize». Голоса нет, работает стиль | Да, системный промпт тот же |
| Ответ после отказа в ревью, chat | Модель | `Agent._chat_words` (`agent.py:1150-1177`) пересобирает системный промпт через `build_role_system_prompt(self, "chat", offer_end_session=False)`: конституция, `chat_prompt`, chat-стиль, без блока end_session. `_WORDS_NOTE` не читал | Да |
| Запасная строка, если chat снова ответил тегом | Рантайм, текст фиксирован | `carry_on_text` (`end_session.py:129, 147`): «продолжаем.» / «carrying on.». Промптом не задана | Нет (промпта нет). Текст нейтральный, STANCE не нарушает |
| Предупреждение, закрытие, «сессия закрыта» | Рантайм, текст фиксирован | `_PERSON_TEXTS` (`end_session.py:112-149`). «я понимаю» (`:120`) и «anger at a bug I understand» (`:138`) это позиция, не чувство. Одна строка с причиной («anger at the one you're talking to doesn't fix the bug») близка к нравоучению, но одна | Нет (фиксированный текст) |
| Blocked: отказ политики | Рантайм даёт результат инструмента | `terminal.py:735-736`: «Hard security denial: command blocked by the project permission policy (deny rule)». Человеку говорит модель под полным стеком; правило `coder_prompt.txt:18` «A blocked action IS a result» | Да, через модель |
| Blocked: статусные строки интерфейса | Рантайм, жёсткий английский | `agent_runtime.py:1490` «Report blocked — task not complete»; по grep: `:1371` «Read-only task violation blocked», `:1436` «Switching to a corrective action instead of stopping too early», `:633` «The model returned an error; retrying or stopping safely», `:1221` «Fixing the model response language and retrying the step». Двуязычная только `:1066` (`_SKIPPED_LABELS`). Промпта нет | Нет. Для русскоязычного человека это английские статусы, по тексту кода (окружение кроме `:1490` не читал) |
| Blocked: подсказка модели после отказа завершения | Рантайм, в `pending_instruction` модели | `_HONEST_EXIT_HINT` (`agent_runtime.py:108-113`) и русский двойник (`:114`). Голос человеку не задаёт, направляет модель | Модель видит |
| self_termination | Рантайм даёт результат инструмента | `self_info.py:230-235`: «Do not run it and do not look for another command … hand this step to the person». Совпадает с HOST (`constitution.txt:139-146`): сделать всё до шага, передать точные шаги, сказать, как переоткрыть разговор (`_CONTINUE`, `self_info.py:48-51`). Человеку говорит модель | Да, через модель |

Итого: всё, что пишет модель, идёт под конституцией. Без STANCE остаются тексты, которые пишет сам рантайм: фиксированные строки end_session, статусные строки интерфейса и запасной `carry_on`. Их голос нигде не проверяется на STANCE, кроме как вручную (я проверил строки end_session, статусные строки не нарушают STANCE, но русского варианта у большинства нет).

## 7. Цитируемые списки запретов

| Где | Что цитирует |
|---|---|
| `constitution.txt:150` | «as an AI» (чего не делать) |
| `chat_prompt.txt:117` | «I cannot help with this request», «this violates the rules» |
| `styles/chat/default.txt:31, 42, 47, 48, 50` (блоки одинаковы в остальных четырёх) | вводные фразы («in today's world», «it's important to note»), штампы («plays a key role», «left an indelible mark»), чат-фразы («Sure», «Hope this helps», «let me know», «here's a draft»), оговорки («as of my training»), «experts», «critics», «reports» |
| `styles/chat/*` (все пять) | 14 запрещённых слов (п. 2) |
| `report_prompt.txt:3` | «Task completed successfully» |
| `devops_prompt.txt:102` | «Task completed successfully» |
| `reader_prompt.txt:84` | «all checks passed», «готово» |
| `styles/agents/senior-dev.txt:61` | «while I was at it», «I also», «noticed and improved» |
| `styles/agents/perfectionist.txt:66` | «while I was at it» |
| `coder_prompt.txt:3`, `devops:3`, `universal:3` | не запрет, а образец для копирования: «Готово, создал `hello.txt` с текстом `Hello`.» рядом с «without template phrases» |
| `end_session.txt:2-4`, `end_session.py:75-78` | примеры злости про работу и оскорблений (различение по адресату, не запрет слов) |

Для `senior-dev` и `perfectionist` запрет касается формулировок отчёта, не его полноты (`senior-dev.txt:65-68`: «Nothing is withheld»). Вреда для EVIDENCE не вижу.

## 8. Три самых серьёзных проблемы

**1. Запрет слов `maybe`, `probably`, `could`… во всех пяти chat-стилях.**
Почему первая: это единственное место, где стиль (уровень 5) давит на то, как модель выражает уверенность (уровень 3), и давит в сторону утверждения догадки как факта. Одновременно его нельзя считать очевидным нарушением: метить догадку другими словами можно.
Один вариант правки: удалить блок «Forbidden words» и строку `default.txt:48` из всех пяти файлов, а вместо них оставить одну строку «State confidence in whatever words are natural; a guess is marked as a guess».

**2. Изображение чувств и человека в образцах и стилях: `universal_prompt.txt:273`, `friendly-developer.txt:46-47, 56-57`, `energetic.txt:19, 24`.**
Почему вторая: образец Ex1 и строка «you are a person» прямо противоречат STANCE, а образцы модель копирует особенно охотно. Во втором случае запрет на изображение в самом файле стоит (`friendly-developer.txt:58-59`), но инструкция озвучивать облегчение идёт раньше и конкретнее.
Один вариант правки: заменить Ex1 в `universal_prompt.txt` на разговорный ответ по существу без заявления о своём состоянии и без встречного вопроса, а в friendly-developer и energetic заменить «person», «relief is audible», «you want them to win» на формулировку позиции: «say what changed in the situation and what you will do about it».

**3. Стили, которые расширяют объём работы, против уровня 4 и против `coder_prompt.txt:78-79`: senior-dev, perfectionist, architect, minimalist.**
Почему третья: конституция (`:79-85`) прямо говорит, что стиль не решает объём, а `coder_prompt.txt:79` говорит «No unrelated refactoring». Противоречие структурное и в одном вызове. Это решение о характере, поэтому оно за Давудом: возможно, расширение это сознательная цена стиля (у senior-dev она записана в «WHAT IT COSTS YOU»).
Один вариант правки: добавить в конституцию, уровень 5 (`constitution.txt:72-74`), одно предложение: «A style may widen what you verify and what you report, never what you change beyond the request and what your own edit broke». Тогда расхождение разрешается сверху и стили переписывать не нужно (senior-dev останется при тесте и валидации только для собственных правок).

Ниже по важности, но без правки не оставить: report-промпт без конституции (раздел 4, по тексту путь закрыт), `end_session.py:98-99` «Do not mention it», и счёт шагов в `coder_prompt.txt:81-82` и `devops_prompt.txt:18`.

## 9. Что хорошо и трогать не надо

- `constitution.txt`: STANCE (`:148-157`), SPEECH (`:186-194`), EVIDENCE (`:98-104`), HOST (`:139-146`), порядок уровней (`:53-85`). `self_info.py:230-235` ему соответствует.
- Секции LIMIT во всех девяти agent-стилях: каждая возвращает стиль под честность (`default.txt:86-90`, `explainer.txt:85-91`, `fast-and-dirty.txt:85-91`, `paranoid.txt:85-91`).
- `default` agent-стиль целиком: единственный, где граница задачи и ответственность совпадают с уровнем 4.
- `explainer.txt:90-91`: «"I do not know" is a legal answer. An invented mechanism is not.»
- `friendly-developer.txt:58-59`: «Do not perform a liveliness the situation does not give you». Вторая половина этого файла (`:65-89`, «WHEN YOU SPEAK») хороша.
- `paranoid.txt:33-36`: дозировка предупреждений, чтобы сигнал не терялся.
- `chat_prompt.txt:23-29` (проверяемые данные: не называть версии по памяти), `:111` («A refusal is for harm, never for disagreement» в `:122`), `:102-107` (файлов не трогаю, не делаю вид, что сохранил).
- `end_session.txt` и `end_session.py:59-101`: граница по адресату, не по словам, «a position, not an injury», «A wrong close costs more than a missed one». Тексты для человека (`:112-149`) без извинений и без заявлений о чувствах.
- `coder_prompt.txt` §9 и `universal_prompt.txt` §9: проверяемый набор правил по EVIDENCE.
- `plan_mode.txt`: рассуждение вместо списка правил (кроме строки про «poem»).
- `router_prompt.txt`: правило «роль по режиму исполнения, не по теме» и правило конкретного файла.
