// Syntax colours for fenced code: syntect parses each line into scopes (the
// grammars come from two-face, which adds TypeScript, TOML, GLSL… to syntect's
// defaults) and the scope names are mapped onto this interface's palette.

use std::sync::OnceLock;

use ratatui::style::Color;
use syntect::parsing::{ParseState, Scope, ScopeStack, SyntaxSet};

use crate::theme::{
    FOG, MIST, MOON, MOSS, ROSEHIP, SYN_COMMENT, SYN_FUNCTION, SYN_KEYWORD, SYN_NUMBER, SYN_OPERATOR, SYN_TYPE,
};

/// A block longer than this is shown uncoloured: parsing it would stall a frame.
const MAX_COLOURED_LINES: usize = 400;

pub struct Token {
    pub text: String,
    pub colour: Color,
    pub italic: bool,
}

fn syntaxes() -> &'static SyntaxSet {
    static SET: OnceLock<SyntaxSet> = OnceLock::new();
    SET.get_or_init(two_face::syntax::extra_newlines)
}

/// Loads the grammars on a worker thread, so the first code block does not
/// cost a frame.
pub fn warm() {
    std::thread::spawn(|| {
        syntaxes();
    });
}

/// The lines of `code` (without their newlines) as coloured tokens; a
/// language syntect does not know leaves them plain.
pub fn highlight(language: &str, code: &str) -> Vec<Vec<Token>> {
    let lines: Vec<&str> = code.strip_suffix('\n').unwrap_or(code).split('\n').collect();
    let set = syntaxes();
    let syntax = (!language.is_empty() && lines.len() <= MAX_COLOURED_LINES)
        .then(|| set.find_syntax_by_token(language))
        .flatten();
    let Some(syntax) = syntax else {
        return lines.iter().map(|line| vec![Token { text: line.expand_tabs(), colour: FOG, italic: false }]).collect();
    };
    let mut state = ParseState::new(syntax);
    let mut stack = ScopeStack::new();
    let mut out = Vec::with_capacity(lines.len());
    for line in lines {
        // The grammars expect each line to end with its newline.
        let source = format!("{line}\n");
        let Ok(ops) = state.parse_line(&source, set) else {
            out.push(vec![Token { text: line.expand_tabs(), colour: FOG, italic: false }]);
            continue;
        };
        let mut tokens: Vec<Token> = Vec::new();
        let mut from = 0;
        let emit = |stack: &ScopeStack, to: usize, from: usize, tokens: &mut Vec<Token>| {
            let end = to.min(line.len());
            if from >= end {
                return;
            }
            let (colour, italic) = colour_of(stack.as_slice());
            let text = line[from..end].expand_tabs();
            match tokens.last_mut() {
                Some(last) if last.colour == colour && last.italic == italic => last.text.push_str(&text),
                _ => tokens.push(Token { text, colour, italic }),
            }
        };
        for (at, op) in &ops {
            emit(&stack, *at, from, &mut tokens);
            from = (*at).min(line.len()).max(from);
            if stack.apply(op).is_err() {
                break;
            }
        }
        emit(&stack, line.len(), from, &mut tokens);
        if tokens.is_empty() {
            tokens.push(Token { text: String::new(), colour: FOG, italic: false });
        }
        out.push(tokens);
    }
    out
}

trait ExpandTabs {
    fn expand_tabs(&self) -> String;
}

impl ExpandTabs for str {
    fn expand_tabs(&self) -> String {
        self.replace('\t', "    ")
    }
}

/// The colour for the innermost scope this palette has an opinion on.
fn colour_of(scopes: &[Scope]) -> (Color, bool) {
    for scope in scopes.iter().rev() {
        if let Some(found) = colour_of_scope(&scope.build_string()) {
            return found;
        }
    }
    (FOG, false)
}

fn colour_of_scope(name: &str) -> Option<(Color, bool)> {
    let starts = |prefix: &str| name.starts_with(prefix);
    let found = if starts("comment") || starts("punctuation.definition.comment") {
        (SYN_COMMENT, true)
    } else if starts("string") || starts("punctuation.definition.string") {
        (MOSS, false)
    } else if starts("constant.character.escape") || starts("keyword.operator") {
        (SYN_OPERATOR, false)
    } else if starts("constant") {
        (SYN_NUMBER, false)
    } else if starts("keyword") || starts("storage") || starts("entity.name.tag") {
        (SYN_KEYWORD, false)
    } else if starts("entity.name.function") || starts("support.function") || starts("meta.function-call") {
        (SYN_FUNCTION, false)
    } else if starts("entity.name") || starts("entity.other") || starts("support.type") || starts("support.class") {
        (SYN_TYPE, false)
    } else if starts("invalid") {
        (ROSEHIP, false)
    } else if starts("punctuation") {
        (MIST, false)
    } else if starts("variable.language") {
        (MOON, false)
    } else {
        return None;
    };
    Some(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn colours(language: &str, code: &str) -> Vec<(String, Color)> {
        highlight(language, code)[0].iter().map(|t| (t.text.clone(), t.colour)).collect()
    }

    #[test]
    fn keywords_strings_and_comments_get_their_own_colours() {
        let tokens = colours("python", "def f(): return \"x\"  # hi");
        let colour_of = |word: &str| tokens.iter().find(|(text, _)| text.contains(word)).map(|(_, colour)| *colour);
        assert_eq!(colour_of("def"), Some(SYN_KEYWORD));
        assert_eq!(colour_of("\"x\""), Some(MOSS));
        assert_eq!(colour_of("hi"), Some(SYN_COMMENT));
    }

    #[test]
    fn extra_grammars_are_present() {
        for language in ["typescript", "toml", "sh", "rust", "json", "glsl"] {
            assert!(syntaxes().find_syntax_by_token(language).is_some(), "{language}");
        }
    }

    #[test]
    fn an_unknown_language_stays_plain_and_keeps_every_line() {
        let lines = highlight("klingon", "a\n\tb\n");
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[1][0].text, "    b");
        assert_eq!(lines[0][0].colour, FOG);
    }

    #[test]
    fn text_is_never_lost_to_the_colouring() {
        let code = "fn main() { let s = \"é\"; // ok\n}";
        let joined: Vec<String> = highlight("rust", code).iter().map(|l| l.iter().map(|t| t.text.as_str()).collect()).collect();
        assert_eq!(joined, ["fn main() { let s = \"é\"; // ok", "}"]);
    }
}
