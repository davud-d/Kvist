// The slash commands offered while the input starts with `/`. The behaviour
// is the Ink app's (frontends/tui/ink/commands.ts `suggest` / `resolveEnter`):
// the list narrows to the names that start with what was typed, the arrows
// walk it round, Tab completes the name, Enter runs the chosen command.

/// A command this interface can run: the ones the core answers over the
/// `command` message, and `/tui`, which the launcher owns.
pub struct Command {
    pub name: &'static str,
    pub en: &'static str,
    pub ru: &'static str,
    /// Running it bare would only print a usage line, so Enter completes it.
    pub needs_argument: bool,
}

impl Command {
    pub fn hint(&self, russian: bool) -> &'static str {
        if russian { self.ru } else { self.en }
    }
}

const fn command(name: &'static str, en: &'static str, ru: &'static str) -> Command {
    Command { name, en, ru, needs_argument: false }
}

pub const COMMANDS: [Command; 19] = [
    command("new", "Start a new session (the current one is kept)", "Начать новую сессию (текущая сохранится)"),
    command("session", "Pick a session, or /session <name> to switch", "Выбрать сессию или /session <имя>"),
    Command { needs_argument: true, ..command("rename", "Set the active session's title", "Задать название сессии") },
    command("role", "Agent role: auto, coder, reader, devops, chat", "Роль агента: auto, coder, reader, devops, chat"),
    command("effort", "Reasoning effort: false … max", "Глубина рассуждений: false … max"),
    command("models", "Pick the fast and smart models", "Выбрать быструю и умную модели"),
    command("web-search-provider", "Web search provider and its API key", "Провайдер веб-поиска и его ключ"),
    command("style", "Working style of the agent and the chat", "Стиль работы агента и чата"),
    command("verbose", "Toggle verbose mode (on | off)", "Подробный режим (on | off)"),
    command("changes", "Files the agent changed, across sessions", "Файлы, которые изменил агент"),
    command("context", "Context size of the model and how much is used", "Размер контекста и сколько занято"),
    command("revert", "Undo the agent's changes", "Отменить изменения агента"),
    command("provider", "Switch or configure the provider", "Сменить или настроить провайдера"),
    command("change-api", "Change the API key of the provider", "Сменить ключ провайдера"),
    command("settings-api", "Request pacing, timeouts, context window", "Темп запросов, таймауты, окно контекста"),
    command("strime", "Streaming for a custom provider (on | off)", "Потоковый ответ своего провайдера (on | off)"),
    command("del-provider", "Delete the active custom provider", "Удалить текущего своего провайдера"),
    command("full-reset", "Erase every saved provider and key", "Стереть всех провайдеров и ключи"),
    command("tui", "Choose the terminal interface", "Выбрать терминальный интерфейс"),
];

/// The commands whose name starts with what follows the `/`; empty once the
/// text has a space (the arguments are being typed) or is not a command.
pub fn suggest(text: &str) -> Vec<&'static Command> {
    let Some(prefix) = text.strip_prefix('/') else { return Vec::new() };
    if prefix.contains(' ') {
        return Vec::new();
    }
    let prefix = prefix.to_lowercase();
    COMMANDS.iter().filter(|command| command.name.starts_with(&prefix)).collect()
}

pub fn name_width() -> usize {
    COMMANDS.iter().map(|command| command.name.len()).max().unwrap_or(0)
}

#[derive(Debug, PartialEq)]
pub enum Enter {
    /// Put this text in the box and wait for the arguments.
    Fill(String),
    /// Send this text.
    Submit(String),
}

/// What Enter does for `text` while the list is open and `selected` is chosen.
pub fn resolve_enter(text: &str, selected: usize) -> Enter {
    match suggest(text).get(selected) {
        None => Enter::Submit(text.to_string()),
        Some(command) if command.needs_argument => Enter::Fill(format!("/{} ", command.name)),
        Some(command) => Enter::Submit(format!("/{}", command.name)),
    }
}

/// First row of a `size`-row window over `count` items that keeps `selected` in view.
pub fn window_start(count: usize, selected: usize, size: usize) -> usize {
    if count <= size {
        return 0;
    }
    selected.saturating_sub(size - 1).min(count - size)
}

/// The row after `selected` (`step` = 1) or before it (-1), wrapping round.
pub fn step(count: usize, selected: usize, step: i32) -> usize {
    if count == 0 {
        return 0;
    }
    (selected as i32 + step).rem_euclid(count as i32) as usize
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(text: &str) -> Vec<&'static str> {
        suggest(text).iter().map(|command| command.name).collect()
    }

    #[test]
    fn a_bare_slash_offers_every_command_and_typing_narrows_the_list() {
        assert_eq!(names("/").len(), COMMANDS.len());
        assert_eq!(names("/s"), ["session", "style", "settings-api", "strime"]);
        assert_eq!(names("/SE"), ["session", "settings-api"]);
        assert!(names("/zzz").is_empty());
    }

    #[test]
    fn plain_text_and_typed_arguments_offer_nothing() {
        assert!(names("fix the tests").is_empty());
        assert!(names("/session feature").is_empty());
        assert!(names("").is_empty());
    }

    #[test]
    fn enter_runs_the_chosen_command_not_the_typed_prefix() {
        assert_eq!(resolve_enter("/ne", 0), Enter::Submit("/new".into()));
        assert_eq!(resolve_enter("/s", 1), Enter::Submit("/style".into()));
    }

    #[test]
    fn enter_completes_a_command_that_needs_an_argument() {
        assert_eq!(resolve_enter("/ren", 0), Enter::Fill("/rename ".into()));
    }

    #[test]
    fn enter_sends_text_nothing_matches_as_it_is() {
        assert_eq!(resolve_enter("/nothing", 0), Enter::Submit("/nothing".into()));
        assert_eq!(resolve_enter("hello", 0), Enter::Submit("hello".into()));
        assert_eq!(resolve_enter("/session feature", 0), Enter::Submit("/session feature".into()));
    }

    #[test]
    fn the_window_follows_the_selection() {
        assert_eq!(window_start(5, 4, 8), 0);
        assert_eq!(window_start(20, 0, 8), 0);
        assert_eq!(window_start(20, 7, 8), 0);
        assert_eq!(window_start(20, 8, 8), 1);
        assert_eq!(window_start(20, 19, 8), 12);
    }

    #[test]
    fn the_arrows_walk_the_list_round() {
        assert_eq!(step(4, 3, 1), 0);
        assert_eq!(step(4, 0, -1), 3);
        assert_eq!(step(0, 0, 1), 0);
    }

    #[test]
    fn every_name_is_unique_and_lowercase() {
        let mut seen = std::collections::HashSet::new();
        for command in &COMMANDS {
            assert!(seen.insert(command.name), "{} twice", command.name);
            assert_eq!(command.name, command.name.to_lowercase());
        }
    }
}
