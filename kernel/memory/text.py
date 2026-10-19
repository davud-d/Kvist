# -*- coding: utf-8 -*-
"""Text forms the store and the search share: the duplicate key and the query terms."""
from __future__ import annotations

import hashlib
import re

_WORD_RE = re.compile(r"\w{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "not", "but", "you", "have",
    "что", "это", "как", "для", "все", "или", "его", "она", "они", "при", "без", "уже", "так", "там", "тут",
    "надо", "нужно", "можно", "если", "чтобы",
})
# A long task text is still one question; past this many distinct terms the
# extra words only add noise to the match.
MAX_QUERY_TERMS = 64


def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalized(text).encode("utf-8")).hexdigest()


def query_terms(text: str) -> list[str]:
    """Distinct search terms, each cut to a stem-like prefix.

    The index is trigram (substring) matching, and Russian inflects nearly
    every noun: "тесты" must find "тестов" and "конфигурации" must find
    "конфигурация". Dropping the last one or two letters of a longer word is
    crude, but it is what makes a substring match survive an ending.
    """
    terms: list[str] = []
    for word in _WORD_RE.findall(text.casefold()):
        if word in _STOPWORDS or word.isdigit():
            continue
        term = _stem(word)
        if term not in terms:
            terms.append(term)
        if len(terms) == MAX_QUERY_TERMS:
            break
    return terms


def _stem(word: str) -> str:
    if len(word) >= 6:
        return word[:-2]
    if len(word) == 5:
        return word[:-1]
    return word
