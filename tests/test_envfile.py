"""The stdlib .env reader/writer used for module secrets."""

from __future__ import annotations

from octoops.core.envfile import format_env, load_env_file, parse_env


def test_parse_basic_and_comments_and_quotes():
    text = (
        "# a comment\n"
        "\n"
        "BRAIN_API_KEY=sk-abc123\n"
        'QUOTED="with spaces"\n'
        "SINGLE='single'\n"
        "noequalsline\n"
        "  SPACED  =  trimmed  \n"
    )
    out = parse_env(text)
    assert out == {
        "BRAIN_API_KEY": "sk-abc123",
        "QUOTED": "with spaces",
        "SINGLE": "single",
        "SPACED": "trimmed",
    }


def test_format_roundtrips_through_parse():
    mapping = {"B": "two", "A": "one", "K": "v=with=equals"}
    text = format_env(mapping)
    assert parse_env(text) == mapping


def test_format_empty_is_empty_string():
    assert format_env({}) == ""


def test_load_missing_file_returns_empty(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == {}


def test_load_reads_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text('BRAIN_API_KEY="k"\n', encoding="utf-8")
    assert load_env_file(p) == {"BRAIN_API_KEY": "k"}


def test_roundtrip_with_quotes_backslash_and_newline():
    # A value containing every char that previously corrupted the round-trip.
    mapping = {
        "QUOTE": 'has "double" quotes',
        "BACK": r"path\to\thing",
        "NEWLINE": "line1\nline2",
        "CARRIAGE": "a\r\nb",
        "MIXED": 'x="y"\nz\\w',
    }
    assert parse_env(format_env(mapping)) == mapping


def test_format_value_stays_single_line():
    # A newline in the value must NOT produce a second physical line.
    text = format_env({"K": "a\nb"})
    assert text.count("\n") == 1  # only the trailing record separator
    assert "\\n" in text


def test_literal_backslash_n_distinct_from_newline():
    # Backslash+n (two chars) must round-trip without becoming a real newline.
    assert parse_env(format_env({"K": "a\\nb"})) == {"K": "a\\nb"}
    assert parse_env(format_env({"K": "a\nb"})) == {"K": "a\nb"}


def test_single_quoted_values_are_taken_literally():
    # Hand-edited single-quoted values keep escapes verbatim (no unescaping).
    assert parse_env(r"K='a\nb'") == {"K": r"a\nb"}
