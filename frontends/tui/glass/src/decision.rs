// The question a best-of run ends with: an `input_request` of kind `choice`
// whose `purpose` is `bestof_decision`. It is shown as the verdict table with
// four actions instead of a plain list, and answers with the same
// `{"request_id", "value"}` a list would.

use serde_json::{Value, json};

use crate::arena::{Arena, ArenaDiff, Verdict, VerdictRow};

const PURPOSE: &str = "bestof_decision";

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Action {
    Accept,
    Diff,
    Other,
    Discard,
}

impl Action {
    /// In the order the buttons stand.
    pub const ALL: [Action; 4] = [Action::Accept, Action::Diff, Action::Other, Action::Discard];

    /// The `value` the core offers for it.
    fn offered_as(self) -> &'static str {
        match self {
            Action::Accept => "accept",
            Action::Diff => "diff",
            Action::Other => "other",
            Action::Discard => "discard",
        }
    }

    /// The letter that does it, in Latin and on a Russian keyboard.
    fn keys(self) -> [char; 2] {
        match self {
            Action::Accept => ['a', 'ф'],
            Action::Diff => ['d', 'в'],
            Action::Other => ['p', 'з'],
            Action::Discard => ['x', 'ч'],
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Page {
    Table,
    Patch,
}

#[derive(Clone, Debug)]
pub struct Decision {
    pub request_id: String,
    /// The buttons the core offered, in their usual order.
    pub actions: Vec<Action>,
    pub action: usize,
    pub verdict: Verdict,
    /// The row the person is on (the winner's to begin with).
    pub row: usize,
    pub page: Page,
    /// The patch the core sent after the last "diff".
    pub patch: Option<ArenaDiff>,
    pub scroll: usize,
    /// The answer is sent and the core has not asked again.
    pub waiting: bool,
}

impl Decision {
    /// `None` when the request is some other question, or this interface has
    /// no verdict to put in front of it: then it is an ordinary list.
    pub fn from_request(event: &Value, arena: &mut Arena) -> Option<Decision> {
        let text = |key: &str| event.get(key).and_then(Value::as_str);
        if text("purpose") != Some(PURPOSE) || text("kind") != Some("choice") || text("run_id") != Some(arena.run_id.as_str()) {
            return None;
        }
        let verdict = arena.verdict.clone()?;
        let offered: Vec<&str> = event.get("options")?.as_array()?.iter().filter_map(|o| o.get("value")?.as_str()).collect();
        let actions: Vec<Action> = Action::ALL.into_iter().filter(|a| offered.contains(&a.offered_as())).collect();
        if actions.is_empty() || text("request_id").is_none_or(str::is_empty) {
            return None;
        }
        let fresh = std::mem::take(&mut arena.diff_fresh);
        let patch = arena.diff.clone();
        let row = verdict.rows.iter().position(|r| r.id == verdict.winner).unwrap_or(0);
        Some(Decision {
            request_id: text("request_id")?.to_string(),
            action: actions.iter().position(|a| *a == Action::Accept).unwrap_or(0),
            actions,
            page: if fresh && patch.is_some() { Page::Patch } else { Page::Table },
            verdict,
            row,
            patch,
            scroll: 0,
            waiting: false,
        })
    }

    pub fn selected(&self) -> Option<&VerdictRow> {
        self.verdict.rows.get(self.row)
    }

    pub fn step_row(&mut self, delta: i32) {
        let last = self.verdict.rows.len().saturating_sub(1) as i32;
        self.row = (self.row as i32 + delta).clamp(0, last) as usize;
    }

    pub fn step_action(&mut self, delta: i32) {
        let last = self.actions.len() as i32 - 1;
        self.action = (self.action as i32 + delta).clamp(0, last) as usize;
    }

    /// The action a typed letter stands for, if the core offered it.
    pub fn hotkey(&self, c: char) -> Option<Action> {
        let c = c.to_lowercase().next().unwrap_or(c);
        self.actions.iter().copied().find(|a| a.keys().contains(&c))
    }

    /// The answer for `action`: accepting takes the winner; the diff and a
    /// pick are about the row the person is on. Picking the winner is the
    /// core's own "other" question, which asks who instead.
    pub fn reply(&self, action: Action) -> Value {
        let row = self.selected().map(|r| r.id.as_str()).filter(|id| !id.is_empty());
        let value = match (action, row) {
            (Action::Diff, Some(id)) => format!("diff:{id}"),
            (Action::Other, Some(id)) if id != self.verdict.winner => format!("pick:{id}"),
            _ => action.offered_as().to_string(),
        };
        json!({"request_id": self.request_id, "value": value})
    }

    /// Enter: the action the buttons are on.
    pub fn accept(&self) -> Option<Value> {
        self.actions.get(self.action).map(|action| self.reply(*action))
    }

    /// Esc: the core takes it as no decision for now.
    pub fn cancel(&self) -> Value {
        json!({"request_id": self.request_id, "value": null})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::arena::{DiffStat, Outcome, Started, Tests};

    fn arena_with_verdict() -> Arena {
        let mut arena = Arena::new(Started { run_id: "r1".into(), task: "t".into(), count: 3, base: "abc".into(), executors: Vec::new() });
        let row = |id: &str, outcome| VerdictRow {
            id: id.into(),
            style: format!("style-{id}"),
            outcome: Some(outcome),
            tests: Tests::default(),
            diff: DiffStat::default(),
            note: String::new(),
        };
        arena.verdict = Some(Verdict {
            run_id: "r1".into(),
            rows: vec![row("e1", Outcome::Finalist), row("e2", Outcome::Winner), row("e3", Outcome::Eliminated)],
            winner: "e2".into(),
            reason: "e2 passes everything".into(),
        });
        arena
    }

    fn request(options: &[&str]) -> Value {
        let options: Vec<Value> = options.iter().map(|v| json!({"value": v, "label": v})).collect();
        json!({"type": "input_request", "request_id": "q1", "kind": "choice", "purpose": "bestof_decision", "run_id": "r1", "options": options})
    }

    #[test]
    fn the_four_actions_answer_with_exactly_the_value_the_core_takes() {
        let mut arena = arena_with_verdict();
        let mut decision = Decision::from_request(&request(&["accept", "diff", "other", "discard"]), &mut arena).unwrap();
        assert_eq!(decision.row, 1, "the winner's row first");
        assert_eq!(decision.reply(Action::Accept), json!({"request_id": "q1", "value": "accept"}));
        assert_eq!(decision.reply(Action::Diff), json!({"request_id": "q1", "value": "diff:e2"}));
        assert_eq!(decision.reply(Action::Other), json!({"request_id": "q1", "value": "other"}));
        assert_eq!(decision.reply(Action::Discard), json!({"request_id": "q1", "value": "discard"}));
        decision.step_row(1);
        assert_eq!(decision.reply(Action::Diff)["value"], "diff:e3");
        assert_eq!(decision.reply(Action::Other)["value"], "pick:e3");
        assert_eq!(decision.reply(Action::Accept)["value"], "accept", "accepting is always about the winner");
        assert_eq!(decision.cancel(), json!({"request_id": "q1", "value": null}));
    }

    #[test]
    fn only_the_offered_actions_are_shown_and_enter_does_the_highlighted_one() {
        let mut arena = arena_with_verdict();
        let mut decision = Decision::from_request(&request(&["accept", "discard", "elsewhere"]), &mut arena).unwrap();
        assert_eq!(decision.actions, [Action::Accept, Action::Discard]);
        assert_eq!(decision.accept().unwrap()["value"], "accept");
        decision.step_action(1);
        assert_eq!(decision.accept().unwrap()["value"], "discard");
        assert_eq!(decision.hotkey('X'), Some(Action::Discard));
        assert_eq!(decision.hotkey('ф'), Some(Action::Accept));
        assert_eq!(decision.hotkey('d'), None, "diff was not offered");
    }

    #[test]
    fn other_questions_and_requests_without_a_verdict_stay_ordinary_lists() {
        let mut arena = arena_with_verdict();
        let mut plain = request(&["accept", "diff"]);
        plain.as_object_mut().unwrap().remove("purpose");
        assert!(Decision::from_request(&plain, &mut arena).is_none());
        let mut other_run = request(&["accept"]);
        other_run["run_id"] = json!("r2");
        assert!(Decision::from_request(&other_run, &mut arena).is_none());
        arena.verdict = None;
        assert!(Decision::from_request(&request(&["accept"]), &mut arena).is_none());
    }

    #[test]
    fn a_fresh_patch_opens_the_patch_page_once() {
        let mut arena = arena_with_verdict();
        arena.diff = Some(ArenaDiff { run_id: "r1".into(), id: "e2".into(), patch: "@@ -1 +1 @@\n-a\n+b".into(), files: vec!["a.py".into()] });
        arena.diff_fresh = true;
        let first = Decision::from_request(&request(&["accept", "diff"]), &mut arena).unwrap();
        assert_eq!(first.page, Page::Patch);
        let again = Decision::from_request(&request(&["accept", "diff"]), &mut arena).unwrap();
        assert_eq!(again.page, Page::Table);
    }
}
