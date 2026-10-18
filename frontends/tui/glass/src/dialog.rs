// The core's dialogs (`input_request`, docs/UI_API.md §7): a choice list with
// a search field, a text or secret field, or a yes/no question. Each one ends
// in exactly one reply carrying its `request_id`.

use serde_json::{Value, json};

#[derive(Clone, Debug, PartialEq)]
pub struct Choice {
    pub value: String,
    pub label: String,
    pub detail: String,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Body {
    Choice { query: String, choices: Vec<Choice>, selected: usize, scroll: usize },
    Text { value: String, secret: bool },
    /// The answers are yes, "always" (only when `always` is offered), no;
    /// `picked` indexes them.
    Confirm { picked: usize, always: bool },
}

/// One of a confirmation's buttons.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Answer {
    Yes,
    Always,
    No,
}

impl Body {
    pub fn answers(&self) -> Vec<Answer> {
        match self {
            Body::Confirm { always: true, .. } => vec![Answer::Yes, Answer::Always, Answer::No],
            Body::Confirm { always: false, .. } => vec![Answer::Yes, Answer::No],
            _ => Vec::new(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Ask {
    pub request_id: String,
    pub title: String,
    pub text: String,
    pub help: String,
    pub body: Body,
}

impl Ask {
    /// Reads an `input_request`; `None` for a kind this interface cannot show.
    pub fn from_request(event: &Value) -> Option<Ask> {
        let field = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("").to_string();
        let current = field("current_value");
        let body = match event.get("kind")?.as_str()? {
            "choice" => {
                let choices: Vec<Choice> = event
                    .get("options")?
                    .as_array()?
                    .iter()
                    .filter_map(|option| {
                        let value = option.get("value")?.as_str()?.to_string();
                        let label = option.get("label").and_then(Value::as_str).unwrap_or(&value).to_string();
                        let detail = option.get("description").and_then(Value::as_str).unwrap_or("").to_string();
                        Some(Choice { value, label, detail })
                    })
                    .collect();
                let selected = choices.iter().position(|c| c.value == current).unwrap_or(0);
                Body::Choice { query: String::new(), choices, selected, scroll: 0 }
            }
            "text" => Body::Text { value: current, secret: false },
            "secret" => Body::Text { value: String::new(), secret: true },
            "confirm" => {
                let always = event.get("remember_pattern").and_then(Value::as_str).is_some_and(|p| !p.is_empty());
                // "No" is the default: an approval is never one stray Enter away.
                Body::Confirm { picked: if always { 2 } else { 1 }, always }
            }
            _ => return None,
        };
        let text = match subject_text(event.get("subject")) {
            Some(subject) => subject,
            None => field("body"),
        };
        Some(Ask { request_id: field("request_id"), title: field("title"), text, help: field("help_text"), body })
            .filter(|ask| !ask.request_id.is_empty())
    }

    /// A choice this interface asks itself (never sent to the core).
    pub fn local_choice(request_id: &str, title: &str, choices: Vec<Choice>, current: &str) -> Ask {
        let selected = choices.iter().position(|c| c.value == current).unwrap_or(0);
        Ask {
            request_id: request_id.to_string(),
            title: title.to_string(),
            text: String::new(),
            help: String::new(),
            body: Body::Choice { query: String::new(), choices, selected, scroll: 0 },
        }
    }

    pub fn picked(&self) -> Option<Answer> {
        let Body::Confirm { picked, .. } = &self.body else { return None };
        self.body.answers().get(*picked).copied()
    }

    pub fn pick(&mut self, answer: Answer) {
        let index = self.body.answers().iter().position(|a| *a == answer);
        if let (Some(index), Body::Confirm { picked, .. }) = (index, &mut self.body) {
            *picked = index;
        }
    }

    /// Indices into `choices` that match the query, in their order.
    pub fn visible(&self) -> Vec<usize> {
        let Body::Choice { query, choices, .. } = &self.body else { return Vec::new() };
        let needle = query.to_lowercase();
        choices
            .iter()
            .enumerate()
            .filter(|(_, c)| needle.is_empty() || format!("{} {}", c.label, c.value).to_lowercase().contains(&needle))
            .map(|(i, _)| i)
            .collect()
    }

    pub fn type_char(&mut self, c: char) {
        match &mut self.body {
            Body::Choice { query, .. } => {
                query.push(c);
                self.refilter();
            }
            Body::Text { value, .. } => value.push(c),
            Body::Confirm { .. } => match c {
                'y' | 'Y' | 'д' | 'Д' => self.pick(Answer::Yes),
                'n' | 'N' | 'н' | 'Н' => self.pick(Answer::No),
                _ => {}
            },
        }
    }

    pub fn paste(&mut self, text: &str) {
        for c in text.chars().filter(|c| !c.is_control()) {
            self.type_char(c);
        }
    }

    pub fn backspace(&mut self) {
        match &mut self.body {
            Body::Choice { query, .. } => {
                query.pop();
                self.refilter();
            }
            Body::Text { value, .. } => {
                value.pop();
            }
            Body::Confirm { .. } => {}
        }
    }

    pub fn clear(&mut self) {
        match &mut self.body {
            Body::Choice { query, .. } => {
                query.clear();
                self.refilter();
            }
            Body::Text { value, .. } => value.clear(),
            Body::Confirm { .. } => {}
        }
    }

    /// Keeps the highlight on a visible choice after the filter changed:
    /// the first match.
    fn refilter(&mut self) {
        let first = self.visible().first().copied();
        if let Body::Choice { selected, scroll, .. } = &mut self.body {
            *selected = first.unwrap_or(0);
            *scroll = 0;
        }
    }

    /// Moves the highlight among visible choices or a confirmation's buttons.
    pub fn step(&mut self, delta: i32) {
        let visible = self.visible();
        let buttons = self.body.answers().len() as i32;
        match &mut self.body {
            Body::Choice { selected, .. } => {
                let Some(at) = visible.iter().position(|i| i == selected) else {
                    *selected = visible.first().copied().unwrap_or(0);
                    return;
                };
                let next = (at as i32 + delta).clamp(0, visible.len() as i32 - 1) as usize;
                *selected = visible[next];
            }
            Body::Confirm { picked, .. } => *picked = (*picked as i32 + delta).clamp(0, buttons - 1) as usize,
            Body::Text { .. } => {}
        }
    }

    /// Highlights the choice shown at `row` of the visible list.
    pub fn select_row(&mut self, row: usize) {
        let visible = self.visible();
        if let (Some(&index), Body::Choice { selected, .. }) = (visible.get(row), &mut self.body) {
            *selected = index;
        }
    }

    /// Keeps the highlight inside a list `rows` tall.
    pub fn follow(&mut self, rows: usize) {
        let visible = self.visible();
        if let Body::Choice { selected, scroll, .. } = &mut self.body {
            let at = visible.iter().position(|i| i == selected).unwrap_or(0);
            if at < *scroll {
                *scroll = at;
            }
            if rows > 0 && at >= *scroll + rows {
                *scroll = at + 1 - rows;
            }
        }
    }

    /// The reply for Enter; `None` when nothing can be chosen (no match).
    pub fn accept(&self) -> Option<Value> {
        let value = match &self.body {
            Body::Choice { choices, selected, .. } => {
                self.visible().contains(selected).then(|| choices[*selected].value.clone())?
            }
            Body::Text { value, .. } => value.clone(),
            Body::Confirm { .. } => {
                let answer = self.picked().unwrap_or(Answer::No);
                return Some(json!({
                    "request_id": self.request_id,
                    "approved": answer != Answer::No,
                    "remember": answer == Answer::Always,
                }));
            }
        };
        Some(json!({"request_id": self.request_id, "value": value}))
    }

    /// The reply for Esc: the core treats it as "cancelled", a refusal for a
    /// confirmation.
    pub fn cancel(&self) -> Value {
        match self.body {
            Body::Confirm { .. } => json!({"request_id": self.request_id, "approved": false}),
            _ => json!({"request_id": self.request_id, "value": null}),
        }
    }
}

/// What an approval is about, as text: the command (a line starting "$ ")
/// and the reason, or the file and the diff of an overwrite.
fn subject_text(subject: Option<&Value>) -> Option<String> {
    let subject = subject?;
    let field = |key: &str| subject.get(key).and_then(Value::as_str).unwrap_or("");
    match field("kind") {
        "command" => Some(format!("$ {}\n{}", field("command"), field("reason")).trim_end().to_string()),
        "overwrite" => Some(format!("{}\n{}", field("path"), field("diff")).trim_end().to_string()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn models() -> Ask {
        Ask::from_request(&json!({
            "type": "input_request", "request_id": "r1", "kind": "choice", "title": "Select model",
            "options": [{"value": "gpt-5.6-luna", "label": "gpt-5.6-luna"}, {"value": "gpt-6-sol", "label": "gpt-6-sol"}, {"value": "gpt-6.1-sol", "label": "gpt-6.1-sol"}],
            "current_value": "gpt-6-sol", "help_text": "", "body": ""
        }))
        .unwrap()
    }

    #[test]
    fn a_choice_starts_on_the_current_value_and_answers_with_its_value() {
        let ask = models();
        assert_eq!(ask.accept(), Some(json!({"request_id": "r1", "value": "gpt-6-sol"})));
    }

    #[test]
    fn typing_filters_and_moves_the_highlight_to_the_first_match() {
        let mut ask = models();
        ask.paste("6.1");
        assert_eq!(ask.visible(), vec![2]);
        assert_eq!(ask.accept(), Some(json!({"request_id": "r1", "value": "gpt-6.1-sol"})));
        ask.paste("zzz");
        assert_eq!(ask.accept(), None);
    }

    #[test]
    fn arrows_stop_at_the_ends() {
        let mut ask = models();
        ask.step(5);
        assert_eq!(ask.accept().unwrap()["value"], "gpt-6.1-sol");
        ask.step(-5);
        assert_eq!(ask.accept().unwrap()["value"], "gpt-5.6-luna");
    }

    #[test]
    fn a_secret_starts_empty_and_escape_cancels_with_null() {
        let mut ask = Ask::from_request(&json!({"request_id": "k", "kind": "secret", "current_value": "x"})).unwrap();
        assert_eq!(ask.body, Body::Text { value: String::new(), secret: true });
        ask.paste("sk-123\n");
        assert_eq!(ask.accept(), Some(json!({"request_id": "k", "value": "sk-123"})));
        assert_eq!(ask.cancel(), json!({"request_id": "k", "value": null}));
    }

    #[test]
    fn a_confirmation_defaults_to_no() {
        let mut ask = Ask::from_request(&json!({"request_id": "c", "kind": "confirm", "title": "Delete?"})).unwrap();
        assert_eq!(ask.accept(), Some(json!({"request_id": "c", "approved": false, "remember": false})));
        ask.type_char('y');
        assert_eq!(ask.accept(), Some(json!({"request_id": "c", "approved": true, "remember": false})));
    }

    #[test]
    fn a_command_approval_shows_the_command_and_can_be_remembered() {
        let mut ask = Ask::from_request(&json!({
            "request_id": "a", "kind": "confirm", "title": "Dangerous command", "body": "",
            "subject": {"kind": "command", "command": "python3 -c 'print(2+2)'", "reason": "runs code"},
            "remember_pattern": "python3 -c *"
        }))
        .unwrap();
        assert_eq!(ask.text, "$ python3 -c 'print(2+2)'\nruns code");
        assert_eq!(ask.picked(), Some(Answer::No));
        ask.step(-1);
        assert_eq!(ask.accept(), Some(json!({"request_id": "a", "approved": true, "remember": true})));
    }

    #[test]
    fn always_is_offered_only_with_a_pattern() {
        let ask = Ask::from_request(&json!({"request_id": "a", "kind": "confirm", "subject": {"kind": "overwrite", "path": "a.py", "diff": "-x\n+y"}})).unwrap();
        assert_eq!(ask.body.answers(), vec![Answer::Yes, Answer::No]);
        assert_eq!(ask.text, "a.py\n-x\n+y");
    }

    #[test]
    fn unknown_kinds_and_missing_ids_are_not_shown() {
        assert!(Ask::from_request(&json!({"request_id": "x", "kind": "colour"})).is_none());
        assert!(Ask::from_request(&json!({"kind": "text"})).is_none());
    }
}
