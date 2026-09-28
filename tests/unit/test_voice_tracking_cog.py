"""VoiceTrackingCog가 재시작 시 길드 음성 상태를 서비스에 넘기는지 확인한다."""

from types import SimpleNamespace

from bot.cogs.voice_tracking import VoiceTrackingCog


class RecordingVoiceService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reconcile_guild_voice_presence(self, *, guild_id, present, now):
        self.calls.append({"guild_id": guild_id, "present": present, "now": now})


def make_channel(channel_id, user_ids, category_id=None):
    category = None if category_id is None else SimpleNamespace(id=category_id)
    return SimpleNamespace(
        id=channel_id,
        category=category,
        voice_states={user_id: object() for user_id in user_ids},
    )


async def test_reconcile_all_guilds_builds_presence_map_per_guild():
    service = RecordingVoiceService()
    cog = VoiceTrackingCog(voice_verification_service=service)
    guild = SimpleNamespace(
        id=111,
        voice_channels=[make_channel(777, [2001, 2002], category_id=555)],
        stage_channels=[make_channel(888, [2003])],
    )
    cog.attach_bot(SimpleNamespace(guilds=[guild]))

    await cog.reconcile_all_guilds()

    assert len(service.calls) == 1
    assert service.calls[0]["guild_id"] == 111
    assert service.calls[0]["present"] == {
        "2001": ("777", "555"),
        "2002": ("777", "555"),
        "2003": ("888", None),
    }
    assert service.calls[0]["now"].tzinfo is not None


async def test_reconcile_all_guilds_without_bot_is_noop():
    service = RecordingVoiceService()
    cog = VoiceTrackingCog(voice_verification_service=service)

    await cog.reconcile_all_guilds()

    assert service.calls == []
