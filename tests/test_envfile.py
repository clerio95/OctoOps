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
