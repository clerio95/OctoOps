from octoops.core.conversations import (
    ConversationStore,
    conversation_key,
)
from octoops.shared.models import TransportSource


def test_key_is_per_transport_and_user():
    k1 = conversation_key(TransportSource.Telegram, "1")
    k2 = conversation_key(TransportSource.WhatsApp, "1")
    assert k1 != k2
    assert k1 == ("telegram", "1")


def test_start_get_end_roundtrip():
    store = ConversationStore()
    key = conversation_key(TransportSource.Telegram, "u")
    assert store.get(key) is None
    conv = store.start(key, command="deadlines", data={"step": "menu"})
    assert conv.command == "deadlines"
    assert store.get(key) is conv
    assert store.active(key) is True
    store.end(key)
    assert store.get(key) is None
    assert store.active(key) is False


def test_data_mutations_persist_across_gets():
    store = ConversationStore()
    key = conversation_key(TransportSource.Telegram, "u")
    store.start(key, command="deadlines", data={"step": "menu"})
    store.get(key).data["step"] = "date"
    assert store.get(key).data["step"] == "date"


def test_conversation_expires_after_ttl():
    # A controllable clock so the test is deterministic (no real sleeping).
    now = [1000.0]
    store = ConversationStore(ttl_seconds=10.0, clock=lambda: now[0])
    key = conversation_key(TransportSource.Telegram, "u")
    store.start(key, command="deadlines")

    now[0] += 5.0  # within TTL
    assert store.get(key) is not None
    store.touch(key)  # resets the clock

    now[0] += 8.0  # 8 < 10 since the touch
    assert store.get(key) is not None

    now[0] += 11.0  # now past TTL
    assert store.get(key) is None  # expired and dropped
