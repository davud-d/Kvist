# -*- coding: utf-8 -*-
"""Checks of the text preparation for the Russian voice."""
from __future__ import annotations

import re
import unittest

from speech_ru import cardinal, plural, speakable_russian


class CardinalTests(unittest.TestCase):
    def test_numbers_are_written_in_words(self):
        for n, words in (
            (0, "ноль"), (1, "один"), (14, "четырнадцать"), (21, "двадцать один"), (100, "сто"),
            (128, "сто двадцать восемь"), (1000, "тысяча"), (2026, "две тысячи двадцать шесть"),
            (21_000, "двадцать одна тысяча"), (1_500_000, "миллион пятьсот тысяч"),
            (3_000_005, "три миллиона пять"),
        ):
            with self.subTest(n=n):
                self.assertEqual(cardinal(n), words)

    def test_one_and_two_take_the_gender_of_their_noun(self):
        self.assertEqual(cardinal(1, feminine=True), "одна")
        self.assertEqual(cardinal(22, feminine=True), "двадцать две")
        self.assertEqual(cardinal(1, neuter=True), "одно")
        self.assertEqual(cardinal(2, neuter=True), "два")

    def test_nouns_follow_the_number(self):
        for n, forms in ((1, "процент"), (2, "процента"), (5, "процентов"), (11, "процентов"), (21, "процент"), (112, "процентов")):
            self.assertEqual(plural(n, "процент", "процента", "процентов"), forms)


class SpeakableTests(unittest.TestCase):
    def test_digits_are_never_left_for_the_model_to_drop(self):
        text = "Готово: 3 файла, встреча в 15:30, 2026 год, 128 тестов, 0 ошибок."
        spoken = speakable_russian(text)
        self.assertNotRegex(spoken, r"\d")
        self.assertEqual(
            spoken,
            "Готово: три файла, встреча в пятнадцать тридцать, две тысячи двадцать шесть год, сто двадцать восемь тестов, ноль ошибок.",
        )

    def test_times_and_percentages(self):
        self.assertEqual(speakable_russian("в 9:05"), "в девять ноль пять")
        self.assertEqual(speakable_russian("в 15:00"), "в пятнадцать ноль-ноль")
        self.assertEqual(speakable_russian("45% заряда"), "сорок пять процентов заряда")
        self.assertEqual(speakable_russian("21 %"), "двадцать один процент")
        self.assertEqual(speakable_russian("5,5%"), "пять точка пять процента")

    def test_numbers_agree_with_common_nouns(self):
        self.assertEqual(speakable_russian("1 минута, 2 строки, 1 окно"), "одна минута, две строки, одно окно")
        self.assertEqual(speakable_russian("2 файла"), "два файла")

    def test_a_number_written_with_spaces_is_one_number(self):
        self.assertEqual(speakable_russian("1 000 000 сообщений"), "миллион сообщений")
        self.assertEqual(speakable_russian("15 000 рублей"), "пятнадцать тысяч рублей")

    def test_versions_and_decimals(self):
        self.assertEqual(speakable_russian("версия 2.5.1"), "версия два точка пять точка один")
        self.assertEqual(speakable_russian("pi 3.14"), "пи три точка один четыре")

    def test_file_names_and_domains_are_said_the_way_they_are_read(self):
        self.assertEqual(speakable_russian("hello.txt"), "хелло точка тэ-икс-тэ")
        self.assertEqual(speakable_russian("config.py в src"), "конфиг точка пай в эс-ар-си")
        self.assertEqual(speakable_russian("example.com"), "экзампл точка ком")

    def test_latin_words_are_spelled_in_cyrillic(self):
        self.assertEqual(speakable_russian("Открыла YouTube"), "Открыла ютуб")
        self.assertEqual(speakable_russian("Открой shop"), "Открой шоп")
        # No vowel, or a short abbreviation in capitals: by letters.
        self.assertEqual(speakable_russian("tmp"), "ти-эм-пи")
        self.assertEqual(speakable_russian("USB"), "ю-эс-би")
        self.assertNotRegex(speakable_russian("Приложение xyzzy работает"), "[A-Za-z]")

    def test_symbols_are_named_or_dropped(self):
        self.assertEqual(speakable_russian("Тесты & сборка"), "Тесты и сборка")
        self.assertEqual(speakable_russian("`код` и **важно**"), "код и важно")
        self.assertEqual(speakable_russian("папка/файл"), "папка слэш файл")
        self.assertNotRegex(speakable_russian("snake_case"), "[A-Za-z_]")

    def test_plain_russian_is_left_alone(self):
        text = "Сейчас посмотрю, что в настройках: всё в порядке!"
        self.assertEqual(speakable_russian(text), text)

    def test_nothing_the_model_cannot_read_survives(self):
        samples = ["Версия v2.0.1, 12 ошибок, 100%!", "См. https://example.com/a?b=1#top", "a.b.c, x86_64, 0xFF"]
        for text in samples:
            with self.subTest(text=text):
                self.assertIsNone(re.search(r"[A-Za-z0-9]", speakable_russian(text)), speakable_russian(text))


if __name__ == "__main__":
    unittest.main()
