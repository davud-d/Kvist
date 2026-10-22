from __future__ import annotations

from kernel.agent.call_labels import call_label, trim_purpose


def test_labels_name_what_the_call_touches_in_the_response_language():
    assert call_label("read_file", {"path": "src/x.py"}, "ru") == "читает src/x.py"
    assert call_label("search_code", {"query": "role", "path": "src/"}, "ru") == 'ищет "role" в src/'
    assert call_label("search_code", {"query": "role"}, "en") == 'searches for "role"'
    assert call_label("replace_in_file", {"path": "a.py", "old_text": "x", "new_text": "y"}, "en") == "edits a.py"
    assert call_label("read_multiple_files", {"paths": ["a.py", "b.py"]}, "ru") == "читает a.py, b.py"
    assert call_label("git_status", {}, "ru") == "смотрит git status"


def test_shell_label_is_the_model_purpose_or_the_command():
    assert call_label("run_shell", {"command": ["pytest", "-q"]}, "ru", purpose="гоняю тесты парсера") == "гоняю тесты парсера"
    assert call_label("run_shell", {"command": ["pytest", "-q"]}, "ru") == "запускает pytest -q"


def test_purpose_is_cut_to_ten_words_not_refused():
    assert trim_purpose("one two three four five six seven eight nine ten eleven twelve") == (
        "one two three four five six seven eight nine ten"
    )
    assert trim_purpose(None) == ""
