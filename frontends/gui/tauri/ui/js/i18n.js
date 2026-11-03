// Interface text in English and Russian. The core's own messages come as
// "English / Русский" (docs/UI_API.md §8c); `localized` picks one half.

const RU = {
  "agents": "АГЕНТЫ",
  "new agent": "НОВЫЙ АГЕНТ",
  "overview": "Обзор",
  "inspector": "Панель",
  "settings": "Настройки",
  "send": "Отправить",
  "pause": "Пауза",
  "stop all": "Стоп",
  "message": "Напиши задачу…",
  "message running": "Агент работает — можно дописать…",
  "asleep": "спит",
  "starting": "запуск",
  "idle": "готов",
  "running": "работает",
  "paused": "пауза",
  "gone": "остановлен",
  "asks": "спрашивает",
  "closed": "закрыт",
  "helpers": "Помощницы",
  "screen": "Экран",
  "jobs": "Задачи",
  "context": "Контекст",
  "no helpers": "Помощниц пока нет. Агент позовёт их сам, когда работу можно разделить.",
  "no screen": "Агент не открывал браузер. Когда откроет — он появится здесь.",
  "no jobs": "Фоновых команд нет.",
  "stop": "Остановить",
  "open in browser": "Открыть в браузере",
  "allow once": "Разрешить один раз",
  "allow always": "Всегда для этого",
  "deny": "Отказать",
  "ok": "ОК",
  "cancel": "Отмена",
  "yes": "Да",
  "no": "Нет",
  "folder": "Папка проекта",
  "browse": "Обзор…",
  "open": "Открыть",
  "thought for": "Думала",
  "thinking": "Думаю",
  "agent": "Агент",
  "interface": "Интерфейс",
  "role": "Роль",
  "effort": "Усилие",
  "style": "Стиль",
  "models": "Модели",
  "provider": "Провайдер",
  "sessions": "Сессии",
  "verbose": "Подробный лог",
  "theme": "Тема",
  "glass": "Прозрачность стекла",
  "projects": "Проекты",
  "add project": "Добавить проект",
  "new session": "Новая сессия",
  "size": "Размер",
  "animations": "Анимации",
  "window transparency": "Прозрачное окно",
  "restarting": "Перезапуск, чтобы применить…",
  "speed": "Анимации",
  "font": "Шрифт",
  "scale": "Масштаб",
  "scanlines": "Развёртка",
  "available between turns": "Доступно между ходами",
  "changes": "Изменения",
  "revert": "Откатить",
  "resume": "Продолжить",
  "rename": "Переименовать",
  "export": "Импорт сессий",
  "api settings": "Настройки API",
  "change key": "Сменить ключ",
  "context left": "контекст",
  "tokens": "токенов",
  "files changed": "Изменённые файлы",
  "no changes": "Файлы не менялись.",
  "closed hint": "Нажми Enter — новая сессия",
  "watching": "наблюдает",
  "offline": "нет связи",
  "wake": "разбудить",
  "empty": "Пока пусто. Напиши задачу — агент начнёт работу.",
  "zoom out": "Все агенты",
  "close tab": "Закрыть",
  "off": "выкл",
};

export const lang = { current: "en" };

export function setLanguage(value) {
  lang.current = value === "ru" ? "ru" : "en";
  document.documentElement.lang = lang.current;
}

export function detectLanguage() {
  return (navigator.language || "en").toLowerCase().startsWith("ru") ? "ru" : "en";
}

export function t(key) {
  return lang.current === "ru" ? (RU[key] ?? key) : key;
}

export function localized(message) {
  const text = String(message ?? "");
  const at = text.indexOf(" / ");
  if (at < 0) return text;
  return lang.current === "ru" ? text.slice(at + 3) : text.slice(0, at);
}
