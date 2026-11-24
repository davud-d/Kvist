use std::sync::Mutex;

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

pub const COMMANDS: [Command; 40] = [
    command("help", "Show the commands", "Показать команды"),
    command("new", "Start a new session (the current one is kept)", "Начать новую сессию (текущая сохранится)"),
    command("session", "Pick a session, or /session <name> to switch", "Выбрать сессию или /session <имя>"),
    command("export", "Import sessions from Claude Code, Codex, OpenCode, Cursor and others", "Импортировать сессии из Claude Code, Codex, OpenCode, Cursor и других"),
    Command { needs_argument: true, ..command("rename", "Set the active session's title", "Задать название сессии") },
    command("role", "Agent role: auto, coder, reader, devops, chat", "Роль агента: auto, coder, reader, devops, chat"),
    command("effort", "Reasoning effort: false … max", "Глубина рассуждений: false … max"),
    command("mode", "File edits: manual asks first, accept_edits does not", "Правки файлов: manual спрашивает, accept_edits — нет"),
    command("models", "Pick the fast and smart models", "Выбрать быструю и умную модели"),
    command("web-search-provider", "Web search provider and its API key", "Провайдер веб-поиска и его ключ"),
    command("style", "Working style of the agent and the chat", "Стиль работы агента и чата"),
    command("verbose", "Toggle verbose mode (on | off)", "Подробный режим (on | off)"),
    command("changes", "Files the agent changed, across sessions", "Файлы, которые изменил агент"),
    Command {
        needs_argument: true,
        ..command("best-of", "N agents try one task apart: /best-of 3 <task>", "N агентов решают одну задачу порознь: /best-of 3 <задача>")
    },
    Command {
        needs_argument: true,
        ..command("council", "Four angles on one question: /council <question>", "Четыре стороны одного вопроса: /council <вопрос>")
    },
    command("context", "Context size of the model and how much is used", "Размер контекста и сколько занято"),
    command("memory", "What Kvist remembers: inspect, why, forget, pause", "Что помнит Kvist: inspect, why, forget, pause"),
    command("doctor", "Check modules, frontends, provider, keys and settings", "Проверить модули, интерфейсы, провайдера, ключи и настройки"),
    command("update", "Install the newest Kvist build (restart afterwards)", "Установить свежую сборку Kvist (потом перезапуск)"),
    command("feedback", "Read the feedback Kvist drafted: send it as a GitHub issue, keep it or discard it", "Прочитать черновик отзыва: отправить как issue на GitHub, оставить или удалить"),
    command("rate", "Mark this session as a good or a bad training example", "Отметить сессию как хороший или плохой пример для обучения"),
    command("permissions", "Command rules (allow, host, ask, deny) and auto mode (not safe)", "Правила для команд (allow, host, ask, deny) и автоматический режим (небезопасно)"),
    command("website", "Build a website: Kvist interviews you, builds it and checks it", "Сделать сайт: Kvist расспросит, соберёт и проверит его"),
    command("gmail", "Sign in to Gmail once and save the token", "Один раз войти в Gmail и сохранить токен"),
    command("telegram", "Sign in to Telegram once and save the session", "Один раз войти в Telegram и сохранить сессию"),
    command("revert", "Undo the agent's changes", "Отменить изменения агента"),
    command("resume", "Continue an interrupted run", "Продолжить прерванный запуск"),
    command("provider", "Switch or configure the provider", "Сменить или настроить провайдера"),
    command("change-api", "Change the API key of the provider", "Сменить ключ провайдера"),
    command("settings-api", "Request pacing, timeouts, context window", "Темп запросов, таймауты, окно контекста"),
    command("strime", "Streaming for a custom provider (on | off)", "Потоковый ответ своего провайдера (on | off)"),
    command("headers", "Extra HTTP headers the provider requires (set | remove | clear)", "Доп. HTTP-заголовки, которые требует провайдер (set | remove | clear)"),
    command("del-provider", "Delete the active custom provider", "Удалить текущего своего провайдера"),
    command("full-reset", "Erase every saved provider and key", "Стереть всех провайдеров и ключи"),
    command("machines", "Saved machines: add one, choose where to work", "Сохранённые машины: добавить, выбрать, где работать"),
    command("remote", "Work on a server over ssh: /remote user@host /folder, or off", "Работать на сервере по ssh: /remote user@host /папка, или off"),
    command("copy", "Copy the last answer to the clipboard (/copy N for older)", "Скопировать последний ответ (/copy N — более старый)"),
    command("clear", "Clear the conversation on screen", "Очистить разговор на экране"),
    command("tui", "Choose the terminal interface", "Выбрать терминальный интерфейс"),
    command("quit", "Exit (also /exit, /bye)", "Выйти (также /exit, /bye)"),
];

/// The commands this interface answers itself (or the core expands as an ordinary request): they are
/// offered whatever the manifest lists, since this core's command table carries none of them.
const LOCAL: [&str; 6] = ["help", "website", "copy", "clear", "tui", "quit"];

/// What `/help` prints: every command and what it does, as Markdown.
pub fn help_text() -> String {
    let russian = crate::strings::russian();
    let rows: Vec<String> = offered().into_iter().map(|command| format!("- `/{}` — {}", command.name, command.hint(russian))).collect();
    format!("**{}**\n\n{}", crate::strings::strings().help_title, rows.join("\n"))
}

/// The names that mean another command.
pub fn canonical(name: &str) -> String {
    let name = name.to_lowercase();
    if name == "exit" || name == "bye" { "quit".to_string() } else { name }
}

/// The commands whose name starts with what follows the `/`; empty once the
/// text has a space (the arguments are being typed) or is not a command.
pub fn suggest(text: &str) -> Vec<&'static Command> {
    let Some(prefix) = text.strip_prefix('/') else { return Vec::new() };
    if prefix.contains(' ') {
        return Vec::new();
    }
    let prefix = prefix.to_lowercase();
    offered().into_iter().filter(|command| command.name.starts_with(&prefix)).collect()
}

// The plain plugins (docs/PLUGINS.md) the core listed in its last manifest.
static PLUGINS: Mutex<Vec<&'static Command>> = Mutex::new(Vec::new());

/// Replace the plugin commands with the manifest's `plugin_commands` (name, description).
pub fn set_plugins(plugins: &[(String, String)]) {
    *PLUGINS.lock().unwrap() = plugin_commands(plugins);
}

// The commands the core said it serves in its last manifest (`slash_commands` marked `via: "core"`),
// in the core's order. `None` before any manifest, when the built-in list is all there is.
static CORE: Mutex<Option<Vec<&'static Command>>> = Mutex::new(None);

/// Replace the core's commands with the manifest's `slash_commands` entries this core carries out
/// (name, description). An empty list leaves the built-in one: the manifest said nothing about serving.
pub fn set_core(commands: &[(String, String)]) {
    let mut core = CORE.lock().unwrap();
    *core = (!commands.is_empty()).then(|| core_commands(commands));
}

/// The core's entries as commands: a name this interface knows keeps its own hints and
/// `needs_argument`; one only the core knows takes the manifest's description in both languages.
fn core_commands(commands: &[(String, String)]) -> Vec<&'static Command> {
    commands
        .iter()
        .map(|(name, description)| {
            COMMANDS.iter().find(|command| command.name == name).unwrap_or_else(|| {
                let hint: &'static str = Box::leak(description.clone().into_boxed_str());
                let name: &'static str = Box::leak(name.clone().into_boxed_str());
                &*Box::leak(Box::new(command(name, hint, hint)))
            })
        })
        .collect()
}

/// True for a typed name the core expands itself, so the line travels as an ordinary request.
pub fn is_goal(name: &str) -> bool {
    name == "website" || PLUGINS.lock().unwrap().iter().any(|command| command.name == name)
}

// The strings live as long as the program: a manifest arrives a few times per run with a handful of names.
fn plugin_commands(plugins: &[(String, String)]) -> Vec<&'static Command> {
    plugins
        .iter()
        .filter(|(name, _)| !COMMANDS.iter().any(|command| command.name == name))
        .map(|(name, description)| {
            let hint: &'static str = Box::leak(if description.is_empty() { "Plugin".into() } else { description.clone().into_boxed_str() });
            let name: &'static str = Box::leak(name.clone().into_boxed_str());
            &*Box::leak(Box::new(command(name, hint, hint)))
        })
        .collect()
}

fn offered() -> Vec<&'static Command> {
    let core = CORE.lock().unwrap();
    let plugins = PLUGINS.lock().unwrap();
    offered_from(core.as_deref(), &plugins)
}

/// The menu's commands: this interface's own, then the ones the core serves in its order (the known
/// ones at their usual place), then the plugins. Without a core list, everything built in.
fn offered_from(core: Option<&[&'static Command]>, plugins: &[&'static Command]) -> Vec<&'static Command> {
    let mut offered: Vec<&'static Command> = match core {
        None => COMMANDS.iter().collect(),
        Some(core) => COMMANDS
            .iter()
            .filter(|command| LOCAL.contains(&command.name) || core.iter().any(|served| served.name == command.name))
            .chain(core.iter().copied().filter(|served| !COMMANDS.iter().any(|command| command.name == served.name)))
            .collect(),
    };
    offered.extend(plugins.iter().copied());
    offered
}

pub fn name_width() -> usize {
    offered().into_iter().map(|command| command.name.len()).max().unwrap_or(0)
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
        assert!(names("/m").contains(&"mode"), "/mode is a core command and belongs in the list");
    }

    #[test]
    fn the_core_decides_which_commands_the_menu_offers() {
        let core = core_commands(&[("mode".into(), String::new()), ("frobnicate".into(), "Frob it".into())]);
        let shown: Vec<&str> = offered_from(Some(&core), &[]).iter().map(|command| command.name).collect();
        assert!(shown.contains(&"mode"), "the core's list brings its own commands");
        assert!(shown.contains(&"frobnicate"), "a command this interface never heard of is offered too");
        assert!(!shown.contains(&"models"), "a command the core did not list is not offered");
        for local in LOCAL {
            assert!(shown.contains(&local), "{local} is answered here, whatever the core lists");
        }
    }

    #[test]
    fn a_command_only_the_core_knows_takes_its_manifest_description() {
        let core = core_commands(&[("frobnicate".into(), "Frob it".into())]);
        let command = offered_from(Some(&core), &[]).into_iter().find(|command| command.name == "frobnicate").expect("offered");
        assert_eq!((command.hint(false), command.hint(true), command.needs_argument), ("Frob it", "Frob it", false));

        // A name this interface knows keeps its own words; the manifest's description is the core's English.
        let core = core_commands(&[("mode".into(), "Choose manual mode or accept edits mode".into())]);
        let command = offered_from(Some(&core), &[]).into_iter().find(|command| command.name == "mode").expect("offered");
        assert!(command.hint(true).starts_with("Правки файлов"));
    }

    #[test]
    fn without_a_core_list_the_built_in_one_is_offered() {
        assert_eq!(offered_from(None, &[]).len(), COMMANDS.len());
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
    fn exit_and_bye_are_quit() {
        assert_eq!(canonical("Bye"), "quit");
        assert_eq!(canonical("exit"), "quit");
        assert_eq!(canonical("New"), "new");
    }

    #[test]
    fn a_plugin_is_offered_unless_it_takes_a_built_in_name() {
        let plugins = plugin_commands(&[("novibecode".into(), "Teach".into()), ("new".into(), String::new()), ("x".into(), String::new())]);
        let shown: Vec<(&str, &str)> = plugins.iter().map(|command| (command.name, command.en)).collect();
        assert_eq!(shown, [("novibecode", "Teach"), ("x", "Plugin")]);
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
