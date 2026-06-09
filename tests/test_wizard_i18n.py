"""Pure-data i18n catalog + translator, and the PT-BR validator path."""

from octoops.wizard import i18n
from octoops.wizard import state as S


def test_every_entry_has_english_fallback():
    # English is the source language and the guaranteed fallback.
    for key, entry in i18n._CATALOG.items():
        assert "en" in entry, f"{key} is missing its English template"


def test_translate_known_key_per_language():
    assert i18n.translate("nav.next", "en") == "Next"
    assert i18n.translate("nav.next", "pt-BR") == "Avançar"


def test_translate_falls_back_to_english_for_missing_language():
    # A real key with no German translation falls back to English text.
    assert i18n.translate("nav.next", "de") == "Next"


def test_translate_unknown_key_returns_the_key():
    assert i18n.translate("does.not.exist", "pt-BR") == "does.not.exist"


def test_translate_interpolates_and_supports_repr_conversion():
    out = i18n.translate("validate.user_id_numeric", "en", value="ab")
    assert out == "'ab' is not a numeric user ID"


def test_translate_bad_format_field_returns_template_unformatted():
    # A missing field must never raise — setup can't crash on a translation bug.
    out = i18n.translate("telegram.err.token", "en")  # {err} not provided
    assert out == "Bot token: {err}"


def test_validators_translate_when_given_a_language():
    # Default language keeps the byte-stable English the older tests assert on...
    assert S.validate_required("") == "required"
    # ...and an explicit pt-BR returns the translated message.
    assert S.validate_required("", "pt-BR") == "obrigatório"
    assert S.validate_port("x", "pt-BR") == "deve ser um número"
    assert "fuso horário" in S.validate_timezone("Mars/Phobos", "pt-BR")
