import pytest

from app.bot.access import is_chat_allowed
from app.config import parse_chat_ids


def test_parse_chat_ids_empty_means_nobody():
    assert parse_chat_ids(None) == frozenset()
    assert parse_chat_ids("") == frozenset()


def test_parse_chat_ids_list_with_spaces_and_negative_ids():
    assert parse_chat_ids("123, -456,") == frozenset({123, -456})


def test_parse_chat_ids_invalid_raises():
    with pytest.raises(ValueError):
        parse_chat_ids("123,abc")


def test_only_listed_chats_are_allowed():
    allowed = frozenset({123})
    assert is_chat_allowed(123, allowed)
    assert not is_chat_allowed(999, allowed)
    assert not is_chat_allowed(123, frozenset())
