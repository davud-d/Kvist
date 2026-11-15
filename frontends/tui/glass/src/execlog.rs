// The journal of one executor, to read: its session is fetched the way a
// switched-to session's history is (`--session-op history`) and its events
// rebuild a throwaway conversation. Nothing here talks back to the core.

use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::{Duration, Instant};

use crate::core::{Core, History};
use crate::model::Session;
use crate::protocol;

/// A journal counts as shown this long ago, so its answers appear whole.
const LONG_AGO: Duration = Duration::from_secs(3600);

pub enum LogState {
    Loading,
    /// Read; `older` says the journal holds more than the newest events shown.
    Ready { older: bool },
    Failed(String),
}

pub struct ExecLog {
    pub id: String,
    pub style: String,
    pub session: Session,
    pub state: LogState,
    incoming: Option<Receiver<Result<History, String>>>,
}

impl ExecLog {
    /// Starts reading `session`; with no core to ask, the log is already failed.
    pub fn open(id: &str, style: &str, session: &str, core: Option<&Core>) -> ExecLog {
        let incoming = core.map(|core| core.history(session));
        let state = if incoming.is_some() { LogState::Loading } else { LogState::Failed(crate::strings::strings().no_core.to_string()) };
        ExecLog { id: id.to_string(), style: style.to_string(), session: Session::default(), state, incoming }
    }

    /// Takes the journal when it has arrived.
    pub fn poll(&mut self, now: Instant) {
        let Some(incoming) = self.incoming.as_ref() else { return };
        match incoming.try_recv() {
            Err(TryRecvError::Empty) => {}
            Ok(Ok(history)) => {
                self.incoming = None;
                self.fill(history, now);
            }
            Ok(Err(error)) => {
                self.incoming = None;
                self.state = LogState::Failed(error);
            }
            Err(TryRecvError::Disconnected) => {
                self.incoming = None;
                self.state = LogState::Failed(String::new());
            }
        }
    }

    fn fill(&mut self, history: History, now: Instant) {
        let past = now.checked_sub(LONG_AGO).unwrap_or(now);
        let mut session = Session::default();
        for event in &history.events {
            for change in protocol::translate(event) {
                session.apply(change, past);
            }
        }
        session.settle();
        self.session = session;
        self.state = LogState::Ready { older: history.has_more };
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Item;
    use serde_json::json;

    #[test]
    fn a_journal_that_arrives_becomes_read_only_steps_and_results() {
        let mut log = ExecLog::open("e1", "senior-dev", "s1", None);
        assert!(matches!(log.state, LogState::Failed(_)), "no core, nothing to read");
        let (sender, incoming) = std::sync::mpsc::channel();
        log.incoming = Some(incoming);
        log.state = LogState::Loading;
        let events = [
            json!({"type": "user", "text": "fix it"}),
            json!({"type": "step", "step": 1, "call_id": "c1", "action_type": "run_shell", "action_description": "runs the tests", "user_message": ""}),
            json!({"type": "action_result", "call_id": "c1", "ok": true, "summary": "exit 0"}),
            json!({"type": "step", "step": 2, "call_id": "c2", "action_type": "read_file", "user_message": ""}),
        ];
        sender.send(Ok(History { events: events.to_vec(), has_more: true })).unwrap();
        log.poll(Instant::now());
        assert!(matches!(log.state, LogState::Ready { older: true }));
        assert!(matches!(&log.session.items[..], [Item::User(_), Item::Tool { .. }, Item::Tool { .. }]));
        assert!(matches!(&log.session.items[1], Item::Tool { label, state: crate::model::CallState::Done { ok: true, .. }, .. } if label == "runs the tests"));
        assert!(matches!(&log.session.items[2], Item::Tool { state: crate::model::CallState::Done { ok: false, .. }, .. }), "a call the journal never finished is shown stopped");
    }

    #[test]
    fn a_failed_read_says_why() {
        let mut log = ExecLog::open("e1", "s", "s1", None);
        let (sender, incoming) = std::sync::mpsc::channel();
        log.incoming = Some(incoming);
        sender.send(Err("unknown session".to_string())).unwrap();
        log.poll(Instant::now());
        assert!(matches!(&log.state, LogState::Failed(why) if why == "unknown session"));
    }
}
