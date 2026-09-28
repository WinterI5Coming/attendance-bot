"""운영 안정성: 채널 전송 실패 분류, 백업 중복 방지, 종료 백업 간격."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from bot.scheduler.backup_loop import BackupScheduler
from bot.utils.discord_channels import deliver_channel_message


def _http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), {"message": "m", "code": 0})


class _Client:
    def __init__(self, channel=None, fetch_error=None):
        self.channel = channel
        self.fetch_error = fetch_error

    def get_channel(self, channel_id):
        return self.channel

    async def fetch_channel(self, channel_id):
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.channel


class _Channel:
    def __init__(self, send_error=None):
        self.send_error = send_error

    async def send(self, **kwargs):
        if self.send_error is not None:
            raise self.send_error
        return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_deleted_channel_is_permanent_failure():
    outcome = await deliver_channel_message(
        _Client(fetch_error=_http_error(discord.NotFound, 404)), "123", content="x"
    )
    assert not outcome.ok and outcome.permanent_failure
    assert outcome.reason == "channel_not_found"


@pytest.mark.asyncio
async def test_missing_send_permission_is_permanent_failure():
    outcome = await deliver_channel_message(
        _Client(channel=_Channel(send_error=_http_error(discord.Forbidden, 403))),
        "123",
        content="x",
    )
    assert not outcome.ok and outcome.permanent_failure
    assert outcome.reason == "send_forbidden"


@pytest.mark.asyncio
async def test_server_error_is_transient_failure():
    outcome = await deliver_channel_message(
        _Client(channel=_Channel(send_error=_http_error(discord.HTTPException, 503))),
        "123",
        content="x",
    )
    assert not outcome.ok and not outcome.permanent_failure


@pytest.mark.asyncio
async def test_unconfigured_channel_is_permanent_failure():
    outcome = await deliver_channel_message(_Client(), None, content="x")
    assert outcome.permanent_failure and outcome.reason == "channel_not_configured"


class _BackupService:
    def __init__(self, *, existing_dates=(), latest=None):
        self.existing_dates = set(existing_dates)
        self.latest = latest
        self.created = 0

    def has_backup_for_date(self, date_key):
        return date_key in self.existing_dates

    def latest_backup_time(self):
        return self.latest

    async def create_backup(self, *, now):
        self.created += 1
        return SimpleNamespace(created=True)


@pytest.mark.asyncio
async def test_restart_does_not_recreate_backup_for_same_day():
    service = _BackupService(existing_dates={"20260702"})
    scheduler = BackupScheduler(backup_service=service)

    created = await scheduler.run_once(datetime(2026, 7, 2, 5, tzinfo=UTC))
    assert created is False and service.created == 0

    next_day = await scheduler.run_once(datetime(2026, 7, 3, 5, tzinfo=UTC))
    assert next_day is True and service.created == 1


@pytest.mark.asyncio
async def test_shutdown_backup_respects_minimum_interval():
    now = datetime(2026, 7, 2, 5, tzinfo=UTC)
    recent = _BackupService(latest=now - timedelta(minutes=10))
    stale = _BackupService(latest=now - timedelta(hours=2))

    assert await BackupScheduler(backup_service=recent).run_shutdown_backup(now) is False
    assert await BackupScheduler(backup_service=stale).run_shutdown_backup(now) is True
    assert recent.created == 0 and stale.created == 1
