// The phone interface speaks English and Russian; the language is the phone's, Russian if it says so.
const STRINGS = {
    en: {
        tagline: 'your agent, from the couch', quiet: 'All quiet.', waiting: n => `${n} waiting for you!`,
        working: n => `${n} at work…`, winterGreeting: 'Brr… cozy day for code.', winterWaiting: n => `${n} waiting, scarf on!`,
        projects: 'PROJECTS', inbox: 'NEEDS YOU', noProjects: 'This computer has no projects yet. Add one in Kvist on the computer.',
        newSession: 'New session', noSessions: 'no sessions yet', untitled: 'New session',
        offline: 'Offline', connecting: 'Connecting…', reconnecting: 'Reconnecting…', online: 'Connected',
        idle: 'Idle', running: 'Working', starting: 'Starting', waitingState: 'Needs you', finished: 'Done', failed: 'Failed', stopped: 'Stopped',
        message: 'Message Kvist…', send: 'Send', stop: 'Stop', pause: 'Pause', pausing: 'Pausing…', back: 'Back',
        you: 'YOU', kvist: 'KVIST', thinking: 'thinking…', thought: s => `thought for ${s}`,
        notSent: 'Not sent', retry: 'Retry', allow: 'Allow', deny: 'Deny', ok: 'OK', remember: 'Remember this safe approval',
        command: 'COMMAND', overwrite: 'OVERWRITE', moreLines: n => `… ${n} more lines`,
        loading: 'Loading…', loadFailed: 'Could not load the conversation.', closed: 'This session is closed. Start a new one.',
        needsInput: 'Kvist needs you', sendFailed: 'Message was not sent.', emptyChat: 'Tell Kvist what to do.',
        scanning: 'Sniffing around the network…', foundN: n => `Found ${n}!`, nothing: 'No computers found.',
        hintRun: 'On the computer, run:', hintNetwork: 'Both must be on the same router.', rescan: 'Search again',
        manual: 'Enter address', address: 'Address, e.g. 192.168.1.20', codeTitle: name => `Code for ${name}`,
        codeHint: 'The computer prints it when you start kvist-web --lan.', connect: 'Connect', cancel: 'Cancel',
        wrongCode: 'That code is not right.', unreachable: 'Cannot reach that computer.', lostSaved: name => `Cannot reach ${name}. Looking for it…`,
        savedMark: 'paired', switchComputer: 'Change computer',
    },
    ru: {
        tagline: 'твой агент с дивана', quiet: 'Тишина.', waiting: n => `Тебя ждут: ${n}!`,
        working: n => `Работает: ${n}…`, winterGreeting: 'Бррр… уютный день для кода.', winterWaiting: n => `Ждут: ${n}, шарф надет!`,
        projects: 'ПРОЕКТЫ', inbox: 'ЖДУТ ТЕБЯ', noProjects: 'На этом компьютере пока нет проектов. Добавь проект в Kvist на компьютере.',
        newSession: 'Новая сессия', noSessions: 'сессий пока нет', untitled: 'Новая сессия',
        offline: 'Нет связи', connecting: 'Подключаюсь…', reconnecting: 'Переподключаюсь…', online: 'Подключено',
        idle: 'Свободна', running: 'Работает', starting: 'Запуск', waitingState: 'Ждёт тебя', finished: 'Готово', failed: 'Ошибка', stopped: 'Остановлена',
        message: 'Напиши Kvist…', send: 'Отправить', stop: 'Стоп', pause: 'Пауза', pausing: 'Пауза…', back: 'Назад',
        you: 'ТЫ', kvist: 'KVIST', thinking: 'думаю…', thought: s => `думала ${s}`,
        notSent: 'Не отправлено', retry: 'Ещё раз', allow: 'Разрешить', deny: 'Отклонить', ok: 'ОК', remember: 'Запомнить безопасное разрешение',
        command: 'КОМАНДА', overwrite: 'ПЕРЕЗАПИСЬ', moreLines: n => `… ещё строк: ${n}`,
        loading: 'Загрузка…', loadFailed: 'Не удалось загрузить разговор.', closed: 'Сессия закрыта. Начни новую.',
        needsInput: 'Kvist нужен ты', sendFailed: 'Сообщение не отправлено.', emptyChat: 'Скажи Kvist, что делать.',
        scanning: 'Принюхиваюсь к сети…', foundN: n => `Нашла: ${n}!`, nothing: 'Компьютеров не видно.',
        hintRun: 'На компьютере запусти:', hintNetwork: 'Оба должны быть на одном роутере.', rescan: 'Искать снова',
        manual: 'Ввести адрес', address: 'Адрес, например 192.168.1.20', codeTitle: name => `Код для ${name}`,
        codeHint: 'Компьютер печатает его при запуске kvist-web --lan.', connect: 'Подключить', cancel: 'Отмена',
        wrongCode: 'Код не подходит.', unreachable: 'Не достучаться до компьютера.', lostSaved: name => `Не достучаться до ${name}. Ищу…`,
        savedMark: 'знакомый', switchComputer: 'Другой компьютер',
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
