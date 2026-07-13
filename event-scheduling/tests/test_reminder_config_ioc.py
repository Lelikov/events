"""Reminder settings defaults + DI resolvability of reminder adapters."""

import pytest
from dishka import Scope

from event_scheduling.config import Settings
from event_scheduling.ioc import AppProvider
from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter


def test_reminder_settings_defaults(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("SCHEDULING_API_KEY", "k")
    s = Settings()
    assert s.reminder_enabled is True
    assert s.reminder_interval_seconds == 60.0
    assert s.reminder_shift_from_minutes == 55
    assert s.reminder_shift_to_minutes == 65
    assert s.reminder_batch_size == 100


@pytest.mark.asyncio
async def test_reminder_adapters_resolvable_in_request_scope(app) -> None:  # noqa: ARG001
    from dishka import make_async_container

    container = make_async_container(AppProvider())
    async with container() as req:
        assert req.scope is Scope.REQUEST
        assert await req.get(IReminderReadAdapter) is not None
        assert await req.get(IReminderWriteAdapter) is not None
    await container.close()
