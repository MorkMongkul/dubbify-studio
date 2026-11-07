"""
Several Gemini API keys in one setting (0.6.0): GEMINI_API_KEY may hold one key or many — one per line or separated by
commas, with or without a label such as `api_key01=`. Duplicates count once, a key is never required to look a certain
way here (the Settings route checks the format), and each key has its own daily and per-minute caps (0 = no cap).
"""
import pytest

from app.core.config import Settings, parse_gemini_keys

A, B, C = ("AIzaSy" + c * 33 for c in "ABC")   # made-up keys, built here so no key-like text is in the source


def test_one_key_per_line():
    assert parse_gemini_keys(f"{A}\n{B}\r\n{C}\n") == [A, B, C]


def test_keys_separated_by_commas_and_spaces_around_them():
    assert parse_gemini_keys(f" {A} ,{B},  {C}") == [A, B, C]


def test_labels_like_api_key01_are_dropped():
    assert parse_gemini_keys(f"api_key01={A}\napi_key02 = {B}\nKEY_3={C}") == [A, B, C]


def test_a_key_given_twice_counts_once_and_keeps_its_place():
    assert parse_gemini_keys(f"{A}\n{B}\n{A}") == [A, B]


def test_empty_lines_and_nothing_at_all():
    assert parse_gemini_keys("") == [] and parse_gemini_keys(None) == [] and parse_gemini_keys(" \n ,\n") == []


def test_the_settings_object_parses_its_own_key(monkeypatch):
    settings = Settings(GEMINI_API_KEY=f"{A}\n{B}")
    assert settings.parsed_gemini_keys() == [A, B]
    assert Settings(GEMINI_API_KEY="").parsed_gemini_keys() == []


def test_each_key_has_default_caps():
    settings = Settings()
    assert (settings.GEMINI_RPM_PER_KEY, settings.GEMINI_RPD_PER_KEY, settings.GEMINI_TPD_PER_KEY) == (12, 450, 900_000)
