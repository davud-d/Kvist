// What the voice screen shows over the orb when the person has something to
// decide: the menu of things to change, and the core's dialogs (a list with a
// search field, a text or secret field, a yes/no question). The dialogs are
// the conversation screen's own `Ask`; this file only turns one into rows of
// text and reads a spoken "yes" or "no" as its answer.

use ratatui::style::{Color, Modifier, Style};
use serde_json::{Value, json};

use crate::dialog::{Answer, Ask, Body};
use crate::voice_strings::Strings;
use crate::text::{self, Line};
use crate::theme;

/// Rows of a list the overlay shows at once; the rest scrolls.
pub const LIST_ROWS: usize = 10;
/// Lines of an explanation (a command and its reason) shown above a dialog's body.
const TEXT_LINES: usize = 5;
const CURSOR: &str = "▏";

/// One thing the menu changes. Each is a command the core already has, and the
/// core's own dialogs do the asking.
pub struct MenuItem {
    pub icon: &'static str,
    /// Pressing this letter in the menu picks the item.
    pub key: char,
    pub command: &'static str,
    pub label: fn(&Strings) -> &'static str,
}

pub const MENU: [MenuItem; 13] = [
    MenuItem { icon: "\u{ea60}", key: 'n', command: "new", label: |s| s.voice_menu_new },
    MenuItem { icon: "\u{eac7}", key: 's', command: "session", label: |s| s.voice_menu_sessions },
    MenuItem { icon: "\u{ec19}", key: 'm', command: "models", label: |s| s.voice_menu_model },
    MenuItem { icon: "\u{eb2d}", key: 'p', command: "provider", label: |s| s.voice_menu_provider },
    MenuItem { icon: "\u{eb11}", key: 'k', command: "change-api", label: |s| s.voice_menu_key },
    MenuItem { icon: "\u{eb01}", key: 'w', command: "web-search-provider", label: |s| s.voice_menu_search },
    MenuItem { icon: "\u{eacd}", key: 'e', command: "effort", label: |s| s.voice_menu_effort },
    MenuItem { icon: "\u{ea67}", key: 'r', command: "role", label: |s| s.voice_menu_role },
    MenuItem { icon: "\u{eb5c}", key: 't', command: "style", label: |s| s.voice_menu_style },
    MenuItem { icon: crate::icons::I_TOOLS, key: 'a', command: "settings-api", label: |s| s.voice_menu_api },
    MenuItem { icon: theme::I_BRANCH, key: 'c', command: "changes", label: |s| s.voice_menu_changes },
    MenuItem { icon: theme::I_CLOSE, key: 'v', command: "revert", label: |s| s.voice_menu_revert },
    // Not a core command: the launcher starts the interface (`Voice::ask_frontend`).
    MenuItem { icon: theme::I_TERMINAL, key: 'i', command: "tui", label: |s| s.voice_menu_interface },
];

/// A line of the overlay: text, and a background the whole row takes when it is highlighted.
pub struct Row {
    pub line: Line,
    pub fill: Option<Color>,
}

impl Row {
    fn plain(line: Line) -> Row {
        Row { line, fill: None }
    }

    fn blank() -> Row {
        Row::plain(Vec::new())
    }
}

fn style(colour: Color) -> Style {
    Style::default().fg(colour)
}

fn bold(colour: Color) -> Style {
    style(colour).add_modifier(Modifier::BOLD)
}

/// A line with `right` pushed to the right edge of `width` columns.
fn spread(mut left: Line, right: Line, width: usize) -> Line {
    let gap = width.saturating_sub(text::line_width(&left) + text::line_width(&right)).max(2);
    left.push((" ".repeat(gap), Style::default()));
    left.extend(right);
    left
}

/// The menu: one row per item with its letter, icon, name and, when known, the current value.
pub fn menu_rows(selected: usize, values: &[String], s: &Strings, width: usize) -> Vec<Row> {
    let mut rows = vec![Row::plain(vec![(format!("{}  {}", theme::I_CHEVRON, s.voice_menu_title), bold(theme::MOON))]), Row::blank()];
    for (index, item) in MENU.iter().enumerate() {
        let picked = index == selected;
        let (name_style, icon_style) = if picked { (bold(theme::MOON), style(theme::MOON)) } else { (style(theme::FOG), style(theme::MIST)) };
        let left: Line = vec![
            (format!("{}  ", item.key), style(theme::DUSK)),
            (format!("{}  ", item.icon), icon_style),
            ((item.label)(s).to_string(), name_style),
        ];
        let value = values.get(index).map(String::as_str).unwrap_or("");
        let right: Line = if value.is_empty() { Vec::new() } else { vec![(text::fit(value, width / 3), style(theme::DUSK))] };
        rows.push(Row { line: spread(left, right, width), fill: picked.then_some(theme::HOVER) });
    }
    rows.push(Row::blank());
    rows.push(Row::plain(vec![(s.voice_hint_menu.to_string(), style(theme::DUSK))]));
    rows
}

/// The input line: the field, what Enter does with it, how to leave it.
pub fn input_rows(line: &str, s: &Strings, width: usize) -> Vec<Row> {
    let shown = text::fit_end(line, width.saturating_sub(4));
    let field: Line = vec![(format!("{}  ", theme::I_EDIT), style(theme::MIST)), (format!("{shown}{CURSOR}"), style(theme::FOG))];
    vec![
        Row { line: field, fill: Some(theme::HOVER) },
        Row::blank(),
        Row::plain(vec![(s.voice_hint_input.to_string(), style(theme::DUSK))]),
    ]
}

fn title_icon(body: &Body) -> &'static str {
    match body {
        Body::Choice { .. } => theme::I_SEARCH,
        Body::Text { .. } => theme::I_EDIT,
        Body::Confirm { .. } => "\u{eb32}",
    }
}

/// A core dialog as rows: the title, what it is about, the body, a hint.
pub fn ask_rows(ask: &Ask, s: &Strings, width: usize) -> Vec<Row> {
    let mut rows = vec![Row::plain(vec![(format!("{}  {}", title_icon(&ask.body), ask.title), bold(theme::MOON))])];
    let explanation = text::wrap(&vec![(ask.text.clone(), style(theme::MIST))], width);
    let explanation: Vec<Line> = explanation.into_iter().take(TEXT_LINES).filter(|line| !line.is_empty()).collect();
    rows.extend(explanation.into_iter().map(Row::plain));
    rows.push(Row::blank());
    match &ask.body {
        Body::Choice { query, selected, scroll, .. } => {
            let visible = ask.visible();
            let field: Line = if query.is_empty() {
                vec![(format!("{}  ", theme::I_SEARCH), style(theme::DUSK)), (format!("{}{CURSOR}", s.voice_type_to_search), style(theme::DUSK))]
            } else {
                vec![(format!("{}  ", theme::I_SEARCH), style(theme::MIST)), (format!("{query}{CURSOR}"), style(theme::FOG))]
            };
            let count: Line = vec![(format!("{}", visible.len()), style(theme::DUSK))];
            rows.push(Row::plain(spread(field, count, width)));
            if visible.is_empty() {
                rows.push(Row::plain(vec![(s.nothing_found.to_string(), style(theme::DUSK))]));
            }
            for &index in visible.iter().skip(*scroll).take(LIST_ROWS) {
                let Body::Choice { choices, .. } = &ask.body else { break };
                let choice = &choices[index];
                let picked = index == *selected;
                let mark = if picked { theme::I_CHEVRON } else { " " };
                let mut line: Line = vec![
                    (format!("{mark} "), style(theme::MOON)),
                    (choice.label.clone(), if picked { bold(theme::MOON) } else { style(theme::FOG) }),
                ];
                if !choice.detail.is_empty() {
                    let room = width.saturating_sub(text::line_width(&line) + 3);
                    line.push((format!("  {}", text::fit(&choice.detail, room)), style(theme::DUSK)));
                }
                rows.push(Row { line, fill: picked.then_some(theme::HOVER) });
            }
            rows.push(Row::blank());
            rows.push(Row::plain(vec![(s.voice_hint_list.to_string(), style(theme::DUSK))]));
        }
        Body::Text { value, secret } => {
            let shown = if *secret { "•".repeat(value.chars().count()) } else { value.clone() };
            let field: Line = vec![(format!("{}  ", theme::I_EDIT), style(theme::MIST)), (format!("{shown}{CURSOR}"), style(theme::FOG))];
            rows.push(Row { line: field, fill: Some(theme::HOVER) });
            if !ask.help.is_empty() {
                rows.push(Row::plain(vec![(text::fit(&ask.help, width), style(theme::DUSK))]));
            }
            rows.push(Row::blank());
            rows.push(Row::plain(vec![(s.voice_hint_text.to_string(), style(theme::DUSK))]));
        }
        Body::Confirm { picked, .. } => {
            let mut buttons: Line = Vec::new();
            for (index, answer) in ask.body.answers().iter().enumerate() {
                let label = match answer {
                    Answer::Yes => s.yes,
                    Answer::Always => s.always,
                    Answer::No => s.no,
                };
                let chosen = index == *picked;
                let look = if chosen { bold(theme::MOON).bg(theme::HOVER) } else { style(theme::MIST) };
                buttons.push((format!("  {label}  "), look));
                buttons.push(("  ".to_string(), Style::default()));
            }
            rows.push(Row::plain(buttons));
            rows.push(Row::blank());
            rows.push(Row::plain(vec![(s.voice_keys.to_string(), style(theme::DUSK))]));
        }
    }
    rows
}

const AFFIRMATIVE: [&str; 20] = [
    "да", "ага", "угу", "конечно", "давай", "ок", "окей", "разрешаю", "разрешить", "выполняй", "отправляй",
    "подтверждаю", "yes", "yeah", "yep", "ok", "okay", "sure", "approve", "confirm",
];
const NEGATIVE: [&str; 12] = ["нет", "не", "отмена", "отменить", "стоп", "запрещаю", "no", "nope", "cancel", "stop", "deny", "reject"];
/// Fragments of an option's value or label that make it the "yes" or the "no" of a dialog.
const YES_OPTION: [&str; 9] = ["yes", "да", "allow", "approve", "accept", "run", "разреш", "принять", "выполн"];
const NO_OPTION: [&str; 7] = ["no", "нет", "deny", "reject", "cancel", "отмен", "запрет"];

/// What spoken words mean as yes or no; `None` when they say neither.
fn yes_or_no(heard: &str) -> Option<bool> {
    let lowered = heard.to_lowercase();
    let words: Vec<&str> = lowered.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).collect();
    if words.iter().any(|w| NEGATIVE.contains(w)) {
        return Some(false);
    }
    words.iter().any(|w| AFFIRMATIVE.contains(w)).then_some(true)
}

/// The reply a spoken "yes" or "no" gives to a dialog, or `None` when the words
/// say neither or the dialog needs typing.
pub fn voice_reply(heard: &str, ask: &Ask) -> Option<Value> {
    let yes = yes_or_no(heard)?;
    match &ask.body {
        // Never "always": that is a stronger answer than the person gave.
        Body::Confirm { .. } => Some(json!({"request_id": ask.request_id, "approved": yes, "remember": false})),
        Body::Choice { choices, .. } => {
            let fragments: &[&str] = if yes { &YES_OPTION } else { &NO_OPTION };
            let found = choices.iter().find(|choice| {
                let (value, label) = (choice.value.to_lowercase(), choice.label.to_lowercase());
                !value.contains("always") && !label.contains("always") && fragments.iter().any(|f| value.contains(f) || label.contains(f))
            });
            found.map(|choice| json!({"request_id": ask.request_id, "value": choice.value}))
        }
        Body::Text { .. } => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::voice_strings::strings;

    fn plain(rows: &[Row]) -> Vec<String> {
        rows.iter().map(|row| row.line.iter().map(|(text, _)| text.as_str()).collect()).collect()
    }

    fn confirm() -> Ask {
        Ask::from_request(&json!({"type": "input_request", "request_id": "c", "kind": "confirm", "title": "Send this?", "body": "to the client"})).unwrap()
    }

    fn models() -> Ask {
        Ask::from_request(&json!({"type": "input_request", "request_id": "m", "kind": "choice", "title": "Select model",
            "options": [{"value": "flash", "label": "deepseek-flash", "description": "fast"}, {"value": "pro", "label": "deepseek-pro"}],
            "current_value": "pro"})).unwrap()
    }

    #[test]
    fn the_menu_lists_every_item_with_its_value_and_highlights_the_picked_one() {
        let values: Vec<String> = ["", "Идея про голос", "deepseek-flash", "deepseek", "", "", "", "", ""].map(String::from).to_vec();
        let rows = menu_rows(2, &values, strings(), 60);
        let text = plain(&rows);
        assert_eq!(rows.len(), MENU.len() + 4);
        assert!(text[0].contains(strings().voice_menu_title));
        assert!(text.iter().any(|row| row.contains("deepseek-flash")));
        assert!(text.iter().any(|row| row.contains("Идея про голос")));
        let filled: Vec<usize> = rows.iter().enumerate().filter(|(_, row)| row.fill.is_some()).map(|(i, _)| i).collect();
        assert_eq!(filled, vec![2 + 2]);
    }

    #[test]
    fn every_menu_item_is_a_command_the_core_has_and_has_its_own_letter() {
        // "tui" is the launcher's, not the core's: the screen opens the interface picker itself.
        let commands = [
            "new", "session", "models", "provider", "change-api", "web-search-provider", "effort", "role", "style", "settings-api",
            "changes", "revert", "tui",
        ];
        let named: Vec<&str> = MENU.iter().map(|item| item.command).collect();
        assert_eq!(named, commands);
        let mut keys: Vec<char> = MENU.iter().map(|item| item.key).collect();
        keys.sort();
        keys.dedup();
        assert_eq!(keys.len(), MENU.len());
    }

    #[test]
    fn a_choice_shows_a_search_field_the_rows_and_the_highlight() {
        let ask = models();
        let rows = ask_rows(&ask, strings(), 60);
        let text = plain(&rows);
        assert!(text[0].contains("Select model"));
        assert!(text.iter().any(|row| row.contains("deepseek-flash") && row.contains("fast")));
        let picked: Vec<&Row> = rows.iter().filter(|row| row.fill.is_some()).collect();
        assert_eq!(picked.len(), 1);
        assert!(picked[0].line.iter().any(|(text, _)| text == "deepseek-pro"), "the dialog starts on the current value");
    }

    #[test]
    fn typing_filters_the_list_and_nothing_found_is_said() {
        let mut ask = models();
        ask.paste("flash");
        let text = plain(&ask_rows(&ask, strings(), 60));
        assert!(text.iter().any(|row| row.contains("flash▏")));
        assert!(!text.iter().any(|row| row.contains("deepseek-pro")));
        ask.paste("zzz");
        assert!(plain(&ask_rows(&ask, strings(), 60)).iter().any(|row| row.contains(strings().nothing_found)));
    }

    #[test]
    fn a_long_list_shows_a_window_of_it() {
        let options: Vec<Value> = (0..40).map(|n| json!({"value": format!("m{n}"), "label": format!("model-{n}")})).collect();
        let ask = Ask::from_request(&json!({"type": "input_request", "request_id": "r", "kind": "choice", "title": "t", "options": options})).unwrap();
        let rows = ask_rows(&ask, strings(), 60);
        assert_eq!(plain(&rows).iter().filter(|row| row.contains("model-")).count(), LIST_ROWS);
    }

    #[test]
    fn a_secret_is_masked_and_a_text_is_not() {
        let mut secret = Ask::from_request(&json!({"type": "input_request", "request_id": "s", "kind": "secret", "title": "API key"})).unwrap();
        secret.paste("sk-12345");
        let text = plain(&ask_rows(&secret, strings(), 60));
        assert!(text.iter().any(|row| row.contains("••••••••▏")));
        assert!(!text.iter().any(|row| row.contains("sk-12345")));
        let mut open = Ask::from_request(&json!({"type": "input_request", "request_id": "t", "kind": "text", "title": "Name"})).unwrap();
        open.paste("abc");
        assert!(plain(&ask_rows(&open, strings(), 60)).iter().any(|row| row.contains("abc▏")));
    }

    #[test]
    fn a_confirmation_shows_its_buttons_and_how_to_answer() {
        let rows = ask_rows(&confirm(), strings(), 60);
        let text = plain(&rows);
        assert!(text.iter().any(|row| row.contains(strings().yes) && row.contains(strings().no)));
        assert!(text.iter().any(|row| row.contains(strings().voice_keys)));
        assert!(text.iter().any(|row| row.contains("to the client")));
    }

    #[test]
    fn a_spoken_yes_or_no_answers_a_confirmation_without_remembering() {
        let ask = confirm();
        assert_eq!(voice_reply("Ну... отправляй", &ask), Some(json!({"request_id": "c", "approved": true, "remember": false})));
        assert_eq!(voice_reply("нет, не надо", &ask), Some(json!({"request_id": "c", "approved": false, "remember": false})));
        assert_eq!(voice_reply("хм, дай подумать", &ask), None);
    }

    #[test]
    fn yes_in_a_choice_is_plain_yes_never_always() {
        let ask = Ask::from_request(&json!({"type": "input_request", "request_id": "r", "kind": "choice", "title": "Run it?", "options": [
            {"value": "always", "label": "Always"}, {"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]})).unwrap();
        assert_eq!(voice_reply("да", &ask), Some(json!({"request_id": "r", "value": "yes"})));
        assert_eq!(voice_reply("no", &ask), Some(json!({"request_id": "r", "value": "no"})));
        assert_eq!(voice_reply("может быть", &ask), None);
    }

    #[test]
    fn a_dialog_that_needs_typing_is_not_answered_by_voice() {
        let ask = Ask::from_request(&json!({"type": "input_request", "request_id": "t", "kind": "text", "title": "Name"})).unwrap();
        assert_eq!(voice_reply("да", &ask), None);
    }
}
