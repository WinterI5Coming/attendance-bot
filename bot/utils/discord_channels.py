"""Discord 채널 조회와 메시지 전송을 감싸는 얇은 헬퍼."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryOutcome:
    """채널 메시지 전송 결과.

    Attributes:
        message: 전송된 메시지. 실패하면 ``None``.
        permanent_failure: 채널이 없거나 권한이 없어 재시도해도 소용없는 실패.
        reason: 로그와 운영자 안내에 쓸 짧은 실패 사유.
    """

    message: Any | None = None
    permanent_failure: bool = False
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.message is not None


async def resolve_messageable(client: Any, channel_id: str | int | None) -> Any | None:
    """채널 ID로 메시지를 보낼 수 있는 채널 객체를 찾는다. 실패하면 ``None``."""

    outcome = await resolve_channel(client, channel_id)
    return outcome[0]


async def resolve_channel(
    client: Any,
    channel_id: str | int | None,
) -> tuple[Any | None, bool, str | None]:
    """채널 객체와 함께 (영구 실패 여부, 사유)를 반환한다."""

    if not channel_id:
        return None, True, "channel_not_configured"
    try:
        channel_id_int = int(channel_id)
    except (TypeError, ValueError):
        logger.warning("Invalid channel id: %s", channel_id)
        return None, True, "invalid_channel_id"

    channel = client.get_channel(channel_id_int)
    if channel is not None:
        return channel, False, None
    try:
        channel = await client.fetch_channel(channel_id_int)
    except discord.NotFound:
        logger.warning("Channel not found (deleted?): channel_id=%s", channel_id)
        return None, True, "channel_not_found"
    except discord.Forbidden:
        logger.warning("No permission to view channel: channel_id=%s", channel_id)
        return None, True, "channel_forbidden"
    except Exception:
        logger.exception("Channel lookup failed: %s", channel_id)
        return None, False, "channel_lookup_error"
    return channel, False, None


async def deliver_channel_message(
    client: Any,
    channel_id: str | int | None,
    **kwargs: Any,
) -> DeliveryOutcome:
    """채널에 메시지를 보내고, 실패 시 재시도 가치가 있는지 함께 알려준다."""

    channel, permanent, reason = await resolve_channel(client, channel_id)
    if channel is None:
        return DeliveryOutcome(permanent_failure=permanent, reason=reason)
    try:
        message = await channel.send(**kwargs)
    except discord.Forbidden:
        logger.warning("No permission to send in channel: channel_id=%s", channel_id)
        return DeliveryOutcome(permanent_failure=True, reason="send_forbidden")
    except discord.NotFound:
        logger.warning("Channel vanished while sending: channel_id=%s", channel_id)
        return DeliveryOutcome(permanent_failure=True, reason="channel_not_found")
    except discord.HTTPException as exc:
        logger.warning(
            "Channel send failed (HTTP %s): channel_id=%s", getattr(exc, "status", "?"), channel_id
        )
        return DeliveryOutcome(permanent_failure=False, reason="http_error")
    except Exception:
        logger.exception("Channel send failed: channel_id=%s", channel_id)
        return DeliveryOutcome(permanent_failure=False, reason="unexpected_error")
    return DeliveryOutcome(message=message)


async def send_channel_message(
    client: Any,
    channel_id: str | int | None,
    **kwargs: Any,
) -> Any | None:
    """채널에 메시지를 보내고 전송된 메시지를 반환한다. 실패하면 ``None``."""

    outcome = await deliver_channel_message(client, channel_id, **kwargs)
    return outcome.message


async def edit_channel_message(
    client: Any,
    channel_id: str | int | None,
    message_id: str | int | None,
    **kwargs: Any,
) -> bool:
    """채널의 기존 메시지를 수정한다. 메시지를 찾지 못하면 ``False``."""

    if not message_id:
        return False
    channel = await resolve_messageable(client, channel_id)
    if channel is None:
        return False
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(**kwargs)
    except (discord.NotFound, discord.Forbidden) as exc:
        logger.warning(
            "Channel message edit skipped (%s): channel_id=%s message_id=%s",
            exc.__class__.__name__,
            channel_id,
            message_id,
        )
        return False
    except Exception:
        logger.exception(
            "Channel message edit failed: channel_id=%s message_id=%s",
            channel_id,
            message_id,
        )
        return False
    return True
