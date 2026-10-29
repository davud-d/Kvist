"use strict"

const STRINGS = {
  en: {
    tooltipIdle: "Kvist",
    tooltipSessions: (count) => `Kvist: ${count} ${count === 1 ? "session" : "sessions"}`,
    tooltipDone: (count) => `Kvist: ${count} finished`,
    menuOpen: "Open chat",
    menuDisable: "Turn off the Kvist icon",
    menuQuit: "Quit (until the next run)",
    emptyTitle: "No Kvist sessions",
    emptyHint: "Start Kvist in a terminal and its session appears here.",
    readOnly: "Read-only. Continue in the terminal.",
    goToTerminal: "Go to terminal",
    disable: "Turn off icon",
    stateRunning: "working",
    stateDone: "finished",
    stateIdle: "idle",
    sessionEnded: "This terminal session has ended.",
    historyFailed: "Could not read the session history",
    focusFailed: "Could not switch to the terminal",
    disableFailed: "Could not turn the icon off",
    barLookupFailed: "Could not locate the status bar",
  },
  ru: {
    tooltipIdle: "Kvist",
    tooltipSessions: (count) => `Kvist: сессий — ${count}`,
    tooltipDone: (count) => `Kvist: завершено — ${count}`,
    menuOpen: "Открыть чат",
    menuDisable: "Отключить иконку Kvist",
    menuQuit: "Закрыть (до следующего запуска)",
    emptyTitle: "Нет сессий Kvist",
    emptyHint: "Запусти Kvist в терминале, и его сессия появится здесь.",
    readOnly: "Только чтение. Продолжай в терминале.",
    goToTerminal: "Перейти в терминал",
    disable: "Отключить иконку",
    stateRunning: "работает",
    stateDone: "завершила",
    stateIdle: "ждёт",
    sessionEnded: "Эта терминальная сессия завершена.",
    historyFailed: "Не удалось прочитать историю сессии",
    focusFailed: "Не удалось переключиться на терминал",
    disableFailed: "Не удалось отключить иконку",
    barLookupFailed: "Не удалось найти панель",
  },
}

function stringsFor(locale) {
  return String(locale).toLowerCase().startsWith("ru") ? STRINGS.ru : STRINGS.en
}

module.exports = { stringsFor }
