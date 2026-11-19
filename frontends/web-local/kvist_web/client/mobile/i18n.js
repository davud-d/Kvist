// The phone interface speaks English and Russian; the language is the phone's, Russian if it says so.
const STRINGS = {
    en: {
        tagline: 'your agent, from the couch', quiet: 'All quiet.', waiting: n => `${n} waiting for you!`,
        working: n => `${n} at work…`, winterGreeting: 'Brr… cozy day for code.', winterWaiting: n => `${n} waiting, scarf on!`,
        projects: 'PROJECTS', thisFolder: 'THIS FOLDER', inbox: 'NEEDS YOU', noProjects: 'No agent is running on this computer. Start Kvist in a folder there, and it shows up here.',
        newSession: 'New session', noSessions: 'no sessions yet', untitled: 'New session',
        offline: 'Offline', connecting: 'Connecting…', reconnecting: 'Reconnecting…', online: 'Connected',
        idle: 'Idle', running: 'Working', starting: 'Starting', waitingState: 'Needs you', finished: 'Done', failed: 'Failed', stopped: 'Stopped',
        message: 'Message Kvist…', send: 'Send', stop: 'Stop', pause: 'Pause', pausing: 'Pausing…', back: 'Back',
        you: 'YOU', kvist: 'KVIST', thinking: 'thinking…', thought: s => `thought for ${s}`,
        notSent: 'Not sent', retry: 'Retry', allow: 'Allow', deny: 'Deny', ok: 'OK', remember: 'Remember this safe approval',
        command: 'COMMAND', overwrite: 'OVERWRITE', moreLines: n => `… ${n} more lines`,
        loading: 'Loading…', loadFailed: 'Could not load the conversation.', closed: 'This session is closed. Start a new one.',
        needsInput: 'Kvist needs you', sendFailed: 'Message was not sent.', emptyChat: 'Tell Kvist what to do.',
        menuTitle: 'Commands', model: 'Model', reasoning: 'Reasoning', role: 'Role', filter: 'Filter', noDiff: 'No changes yet.',
        unknownCommand: name => `Unknown command ${name}. Nothing was sent.`, computerOnly: 'Do this on the computer.',
        needsQuestion: name => `Write the question after ${name}. Nothing was sent.`,
        busyNow: 'Kvist is working. Change this when it stops.', modelHint: 'For this session and new ones.', modelsFailed: 'Could not load the list; showing the built-in models.',
        commandDone: 'Done.', more: 'Commands',
        scanning: 'Sniffing around the network…', foundN: n => `Found ${n}!`, nothing: 'No computers found.',
        hintRun: 'On the computer, start Kvist (any agent):', hintNetwork: 'Both must be on the same router.', rescan: 'Search again',
        manual: 'Enter address', address: 'Address, e.g. 192.168.1.20', connect: 'Connect', cancel: 'Cancel',
        asking: name => `Allow this phone on ${name}…`, denied: 'The computer said no.',
        unavailable: 'That computer cannot ask its owner: no desktop window.', busy: 'Another phone is asking right now.',
        cooldown: 'Wait half a minute and try again.', forgotten: 'This computer no longer knows the phone. Tap it to ask again.',
        expired: 'Nobody answered on the computer in time.', hintCommand: 'kvist', unreachable: 'Cannot reach that computer.', lostSaved: name => `Cannot reach ${name}. Looking for it…`,
        savedMark: 'paired', switchComputer: 'Change computer',
        tabActivity: 'ACTIVITY', tabChanges: 'CHANGES', nothingNeeds: 'Nothing needs you right now.', otherSessions: 'OTHER SESSIONS',
        noCalls: 'No tool calls yet.', noOutput: 'No output.', refresh: 'Refresh', openDesk: 'Activity and changes',
        pickSession: 'Pick a session on the left, or start a new one.',
    },
    ru: {
        tagline: 'твой агент с дивана', quiet: 'Тишина.', waiting: n => `Тебя ждут: ${n}!`,
        working: n => `Работает: ${n}…`, winterGreeting: 'Бррр… уютный день для кода.', winterWaiting: n => `Ждут: ${n}, шарф надет!`,
        projects: 'ПРОЕКТЫ', thisFolder: 'ЭТА ПАПКА', inbox: 'ЖДУТ ТЕБЯ', noProjects: 'На этом компьютере не работает ни один агент. Запусти Kvist в папке на компьютере, и она появится здесь.',
        newSession: 'Новая сессия', noSessions: 'сессий пока нет', untitled: 'Новая сессия',
        offline: 'Нет связи', connecting: 'Подключаюсь…', reconnecting: 'Переподключаюсь…', online: 'Подключено',
        idle: 'Свободна', running: 'Работает', starting: 'Запуск', waitingState: 'Ждёт тебя', finished: 'Готово', failed: 'Ошибка', stopped: 'Остановлена',
        message: 'Напиши Kvist…', send: 'Отправить', stop: 'Стоп', pause: 'Пауза', pausing: 'Пауза…', back: 'Назад',
        you: 'ТЫ', kvist: 'KVIST', thinking: 'думаю…', thought: s => `думала ${s}`,
        notSent: 'Не отправлено', retry: 'Ещё раз', allow: 'Разрешить', deny: 'Отклонить', ok: 'ОК', remember: 'Запомнить безопасное разрешение',
        command: 'КОМАНДА', overwrite: 'ПЕРЕЗАПИСЬ', moreLines: n => `… ещё строк: ${n}`,
        loading: 'Загрузка…', loadFailed: 'Не удалось загрузить разговор.', closed: 'Сессия закрыта. Начни новую.',
        needsInput: 'Kvist нужен ты', sendFailed: 'Сообщение не отправлено.', emptyChat: 'Скажи Kvist, что делать.',
        menuTitle: 'Команды', model: 'Модель', reasoning: 'Рассуждение', role: 'Роль', filter: 'Фильтр', noDiff: 'Изменений пока нет.',
        unknownCommand: name => `Неизвестная команда ${name}. Ничего не отправлено.`, computerOnly: 'Это делается на компьютере.',
        needsQuestion: name => `Напиши вопрос после ${name}. Ничего не отправлено.`,
        busyNow: 'Kvist работает. Измени это, когда он остановится.', modelHint: 'Для этой сессии и новых.', modelsFailed: 'Список не загрузился, показаны встроенные модели.',
        commandDone: 'Готово.', more: 'Команды',
        scanning: 'Принюхиваюсь к сети…', foundN: n => `Нашла: ${n}!`, nothing: 'Компьютеров не видно.',
        hintRun: 'На компьютере запусти Kvist (любой агент):', hintNetwork: 'Оба должны быть на одном роутере.', rescan: 'Искать снова',
        manual: 'Ввести адрес', address: 'Адрес, например 192.168.1.20', connect: 'Подключить', cancel: 'Отмена',
        asking: name => `Разреши этот телефон на ${name}…`, denied: 'Компьютер сказал нет.',
        unavailable: 'Тот компьютер не может спросить владельца: нет окна рабочего стола.', busy: 'Сейчас спрашивает другой телефон.',
        cooldown: 'Подожди полминуты и попробуй снова.', forgotten: 'Компьютер больше не знает этот телефон. Нажми на него, чтобы спросить заново.',
        expired: 'На компьютере не ответили вовремя.', hintCommand: 'kvist', unreachable: 'Не достучаться до компьютера.', lostSaved: name => `Не достучаться до ${name}. Ищу…`,
        savedMark: 'знакомый', switchComputer: 'Другой компьютер',
        tabActivity: 'ДЕЙСТВИЯ', tabChanges: 'ПРАВКИ', nothingNeeds: 'Сейчас от тебя ничего не нужно.', otherSessions: 'ДРУГИЕ СЕССИИ',
        noCalls: 'Вызовов пока нет.', noOutput: 'Вывода нет.', refresh: 'Обновить', openDesk: 'Действия и правки',
        pickSession: 'Выбери сессию слева или начни новую.',
    },
};

export const lang = (navigator.language || 'en').toLowerCase().startsWith('ru') ? 'ru' : 'en';
const table = STRINGS[lang];

// A string or a function of its arguments; a missing key shows itself, so a gap is visible and not blank.
export function t(key, ...args) {
    const value = table[key];
    if (value === undefined) return key;
    return typeof value === 'function' ? value(...args) : value;
}
