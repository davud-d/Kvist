// Word boundaries for the input box (Alt or Ctrl with the arrows, Backspace
// and Delete). A word is a run of letters, digits and `_`, in any script; the
// positions are character indices, like the input's cursor.

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Where the word before `cursor` starts: the separators right before the
/// cursor are skipped first, then the word.
pub fn word_start(text: &str, cursor: usize) -> usize {
    let chars: Vec<char> = text.chars().collect();
    let mut at = cursor.min(chars.len());
    while at > 0 && !is_word(chars[at - 1]) {
        at -= 1;
    }
    while at > 0 && is_word(chars[at - 1]) {
        at -= 1;
    }
    at
}

/// Where the word after `cursor` ends.
pub fn word_end(text: &str, cursor: usize) -> usize {
    let chars: Vec<char> = text.chars().collect();
    let mut at = cursor.min(chars.len());
    while at < chars.len() && !is_word(chars[at]) {
        at += 1;
    }
    while at < chars.len() && is_word(chars[at]) {
        at += 1;
    }
    at
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_start_skips_separators_then_the_word() {
        let text = "fix the  тесты, now";
        assert_eq!(word_start(text, text.chars().count()), 16);
        assert_eq!(word_start(text, 16), 9);
        assert_eq!(word_start(text, 9), 4);
        assert_eq!(word_start(text, 5), 4);
        assert_eq!(word_start(text, 0), 0);
    }

    #[test]
    fn the_end_skips_separators_then_the_word() {
        let text = "fix the  тесты, now";
        assert_eq!(word_end(text, 0), 3);
        assert_eq!(word_end(text, 3), 7);
        assert_eq!(word_end(text, 7), 14);
        assert_eq!(word_end(text, 14), 19);
        assert_eq!(word_end(text, 19), 19);
    }

    #[test]
    fn punctuation_and_underscores_split_words_the_way_a_shell_does() {
        assert_eq!(word_start("src/my_file.rs", 14), 12);
        assert_eq!(word_start("src/my_file.rs", 11), 4);
        assert_eq!(word_end("src/my_file.rs", 0), 3);
    }
}
