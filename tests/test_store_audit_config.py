from datetime import date, datetime, timedelta, timezone

import pytest

from foundation.audit import AuditLog
from foundation.config import ConfigError, Settings
from foundation.store import Conflict, MemoryStore, NotFound


def test_insert_get_query():
    s = MemoryStore()
    s.insert("t", "p", "b", {"n": 2})
    s.insert("t", "p", "a", {"n": 1})
    s.insert("t", "other", "c", {"n": 3})
    assert s.get("t", "p", "a").data == {"n": 1}
    assert [r.rk for r in s.query("t", "p")] == ["a", "b"]


def test_insert_twice_conflicts():
    s = MemoryStore()
    s.insert("t", "p", "a", {})
    with pytest.raises(Conflict):
        s.insert("t", "p", "a", {})


def test_replace_requires_current_etag():
    s = MemoryStore()
    first = s.insert("t", "p", "a", {"v": 1})
    s.replace("t", first, {"v": 2})
    with pytest.raises(Conflict):
        s.replace("t", first, {"v": 3})  # stale etag
    assert s.get("t", "p", "a").data == {"v": 2}


def test_get_missing():
    with pytest.raises(NotFound):
        MemoryStore().get("t", "p", "a")


def test_oversized_record_rejected():
    with pytest.raises(ValueError):
        MemoryStore().insert("t", "p", "a", {"x": "y" * 40_000})


def test_audit_is_ordered_by_day():
    times = iter(
        datetime(2026, 9, 30, h, tzinfo=timezone.utc) + timedelta(microseconds=1) for h in (23, 9, 18)
    )
    audit = AuditLog(MemoryStore(), lambda: next(times))
    audit.record("Finance", "settlement.prepared", "week-39")
    audit.record("Catalog", "listing.updated", "SKU1", {"field": "title"})
    audit.record("Support", "reply.sent", "msg-7")
    events = audit.for_day(date(2026, 9, 30))
    assert [e.actor for e in events] == ["Catalog", "Support", "Finance"]
    assert events[0].details == {"field": "title"}
    assert audit.for_day(date(2026, 10, 1)) == []


BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "tok",
    "TELEGRAM_WEBHOOK_SECRET": "sec",
    "TELEGRAM_OWNER_CHAT_ID": "42",
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
}


def test_settings_load_and_hide_secrets():
    s = Settings.from_env({**BASE_ENV, "AZURE_OPENAI_API_KEY": "key"})
    assert s.telegram_owner_chat_id == 42
    assert s.tables_connection_string == "UseDevelopmentStorage=true"
    assert "tok" not in repr(s) and "sec" not in repr(s) and "key" not in repr(s) and "Development" not in repr(s)


def test_settings_endpoint_wins_over_connection_string():
    s = Settings.from_env({**BASE_ENV, "DATA_TABLES_ENDPOINT": "https://x.table.core.windows.net"})
    assert s.tables_connection_string is None


def test_settings_report_all_missing():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({})
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_OWNER_CHAT_ID", "DATA_TABLES_ENDPOINT"):
        assert name in str(exc.value)


def test_settings_bad_chat_id():
    with pytest.raises(ConfigError):
        Settings.from_env({**BASE_ENV, "TELEGRAM_OWNER_CHAT_ID": "me"})
