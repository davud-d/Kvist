//! Token, cache and cost totals of a session and of the sessions below it
//! (`kernel/session/session_usage.py`).
//!
//! The two implementations must give the same numbers to the last bit, so the order of the
//! additions is part of the contract: a session's own totals first, then its descendants
//! depth first, children in name order, each session once.

use std::collections::{HashMap, HashSet};

use crate::json::{Object, Value};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Usage {
    input_tokens: i64,
    output_tokens: i64,
    cache_read_tokens: Option<i64>,
    cache_write_tokens: Option<i64>,
    calls: i64,
    cost_usd: Option<f64>,
    cost_approx: bool,
}

/// `value` where it is a non-negative integer (`type(value) is int`), else unknown.
fn count(value: Option<&Value>) -> Option<i64> {
    value.and_then(Value::as_int).filter(|number| *number >= 0)
}

/// A finite `int` or `float`; a bool, a string or NaN is unknown.
fn cost(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(number @ Value::Number(_)) => number.as_float().filter(|cost| cost.is_finite()),
        _ => None,
    }
}

/// `left + right` where unknown is skipped and only unknown + unknown is unknown.
fn sum_known<T: Copy>(left: Option<T>, right: Option<T>, add: impl Fn(T, T) -> T) -> Option<T> {
    match (left, right) {
        (Some(left), Some(right)) => Some(add(left, right)),
        (left, None) => left,
        (None, right) => right,
    }
}

impl Usage {
    /// The totals a session document holds, or `None` when it holds none.
    fn from_document(value: &Value) -> Option<Usage> {
        let fields = value.as_object()?;
        Some(Usage {
            input_tokens: count(fields.get("input_tokens")).unwrap_or(0),
            output_tokens: count(fields.get("output_tokens")).unwrap_or(0),
            cache_read_tokens: count(fields.get("cache_read_tokens")),
            cache_write_tokens: count(fields.get("cache_write_tokens")),
            calls: count(fields.get("calls")).unwrap_or(0),
            cost_usd: cost(fields.get("cost_usd")),
            cost_approx: fields.get("cost_approx").is_some_and(Value::is_true),
        })
    }

    fn merged(&self, other: &Usage) -> Usage {
        let integers = |left: Option<i64>, right: Option<i64>| sum_known(left, right, i64::saturating_add);
        Usage {
            input_tokens: self.input_tokens.saturating_add(other.input_tokens),
            output_tokens: self.output_tokens.saturating_add(other.output_tokens),
            cache_read_tokens: integers(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens: integers(self.cache_write_tokens, other.cache_write_tokens),
            calls: self.calls.saturating_add(other.calls),
            cost_usd: sum_known(self.cost_usd, other.cost_usd, |left, right| left + right),
            cost_approx: self.cost_approx || other.cost_approx,
        }
    }

    /// The totals as a status item reports them; unknown cache counts and cost are left out.
    pub fn to_value(&self) -> Value {
        let mut fields = vec![
            ("input_tokens".to_string(), Value::from(self.input_tokens)),
            ("output_tokens".to_string(), Value::from(self.output_tokens)),
        ];
        for (key, tokens) in [("cache_read_tokens", self.cache_read_tokens), ("cache_write_tokens", self.cache_write_tokens)] {
            if let Some(tokens) = tokens {
                fields.push((key.to_string(), Value::from(tokens)));
            }
        }
        fields.push(("calls".to_string(), Value::from(self.calls)));
        if let Some(cost) = self.cost_usd {
            fields.push(("cost_usd".to_string(), Value::float(cost)));
        }
        fields.push(("cost_approx".to_string(), Value::Bool(self.cost_approx)));
        Value::Object(Object::from_pairs(fields))
    }
}

/// The session's own totals, when its document holds any.
pub fn own_totals(document: &Object) -> Option<Usage> {
    document.get("usage_totals").and_then(Usage::from_document)
}

/// Each session's own totals plus those of every session below it, for the sessions that have any.
///
/// A session is below another when its `service.parent_session` names it, at any depth; a loop
/// in the parent links cannot hang the sum.
pub fn family_totals<'a>(documents: &[(&'a str, &'a Object)]) -> HashMap<&'a str, Usage> {
    let mut ordered: Vec<&(&str, &Object)> = documents.iter().collect();
    ordered.sort_by_key(|(name, _)| *name);
    let mut own: HashMap<&str, Usage> = HashMap::new();
    let mut children: HashMap<&str, Vec<&str>> = HashMap::new();
    for &(name, document) in ordered {
        if let Some(usage) = own_totals(document) {
            own.insert(name, usage);
        }
        let parent = document
            .get("service")
            .and_then(Value::as_object)
            .and_then(|service| service.get("parent_session"))
            .and_then(Value::as_str);
        if let Some(parent) = parent {
            children.entry(parent).or_default().push(name);
        }
    }
    if own.is_empty() {
        return HashMap::new();
    }
    documents
        .iter()
        .filter_map(|&(name, _)| sum_below(name, &own, &children).map(|total| (name, total)))
        .collect()
}

fn sum_below(root: &str, own: &HashMap<&str, Usage>, children: &HashMap<&str, Vec<&str>>) -> Option<Usage> {
    let mut total: Option<Usage> = None;
    let mut seen: HashSet<&str> = HashSet::new();
    let mut pending = vec![root];
    while let Some(name) = pending.pop() {
        if !seen.insert(name) {
            continue;
        }
        if let Some(usage) = own.get(name) {
            total = Some(match total {
                None => usage.clone(),
                Some(sum) => sum.merged(usage),
            });
        }
        if let Some(below) = children.get(name) {
            pending.extend(below.iter().rev());
        }
    }
    total
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json;

    fn object(text: &str) -> Object {
        match json::parse(text) {
            Some(Value::Object(object)) => object,
            other => panic!("not an object: {other:?}"),
        }
    }

    fn session(parent: Option<&str>, totals: Option<&str>) -> Object {
        let service = parent.map_or(String::new(), |parent| format!(r#""service": {{"kind": "executor", "parent_session": "{parent}"}},"#));
        let usage = totals.map_or(String::new(), |totals| format!(r#""usage_totals": {totals},"#));
        object(&format!("{{{service}{usage}\"title\": null}}"))
    }

    fn family<'a>(documents: &'a [(&'a str, Object)]) -> HashMap<&'a str, Usage> {
        let pairs: Vec<(&str, &Object)> = documents.iter().map(|(name, document)| (*name, document)).collect();
        family_totals(&pairs)
    }

    #[test]
    fn a_damaged_field_reads_as_unknown_or_zero() {
        let usage = Usage::from_document(&Value::Object(object(
            r#"{"input_tokens": -5, "output_tokens": 1.5, "cache_read_tokens": true, "cache_write_tokens": 7,
                "calls": "3", "cost_usd": NaN, "cost_approx": 1}"#,
        )))
        .unwrap();
        assert_eq!(usage, Usage { cache_write_tokens: Some(7), ..Usage::default() });
        assert!(Usage::from_document(&Value::from("text")).is_none());
        assert_eq!(cost(json::parse("2").as_ref()), Some(2.0));
        assert_eq!(cost(json::parse("true").as_ref()), None);
    }

    #[test]
    fn unknown_stays_unknown_when_totals_are_added() {
        let known = Usage { cache_read_tokens: Some(4), cost_usd: Some(0.5), ..Usage::default() };
        let silent = Usage { cost_approx: true, ..Usage::default() };
        let sum = known.merged(&silent);
        assert_eq!((sum.cache_read_tokens, sum.cache_write_tokens), (Some(4), None));
        assert_eq!((sum.cost_usd, sum.cost_approx), (Some(0.5), true));
        assert_eq!(silent.merged(&silent).cost_usd, None);
    }

    #[test]
    fn a_parent_sums_its_children_and_a_grandchild() {
        let documents = [
            ("lead", session(None, Some(r#"{"input_tokens": 10, "calls": 1, "cost_usd": 0.25}"#))),
            ("exec-1", session(Some("lead"), Some(r#"{"input_tokens": 5, "calls": 2}"#))),
            ("exec-2", session(Some("lead"), None)),
            ("grandchild", session(Some("exec-2"), Some(r#"{"input_tokens": 1, "calls": 1, "cost_usd": 0.5}"#))),
            ("other", session(None, None)),
        ];
        let family = family(&documents);
        assert_eq!(family["lead"].input_tokens, 16);
        assert_eq!(family["lead"].calls, 4);
        assert_eq!(family["lead"].cost_usd, Some(0.75));
        assert_eq!(family["exec-2"].input_tokens, 1);
        assert!(!family.contains_key("other"));
    }

    #[test]
    fn a_loop_in_the_parent_links_ends() {
        let documents = [
            ("a", session(Some("b"), Some(r#"{"input_tokens": 1}"#))),
            ("b", session(Some("a"), Some(r#"{"input_tokens": 2}"#))),
            ("c", session(Some("c"), Some(r#"{"input_tokens": 4}"#))),
        ];
        let family = family(&documents);
        assert_eq!((family["a"].input_tokens, family["b"].input_tokens, family["c"].input_tokens), (3, 3, 4));
    }
}
