"""Discord 채널 조회와 메시지 전송을 감싸는 얇은 헬퍼."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def resolve_messageable(client: Any, channel_id: str | int | None) -> Any | None:
    """채널 ID로 메시지를 보낼 수 있는 채널 객체를 찾는다. 실패하면 ``None``."""

    if not channel_id:
        return None
    try:
        channel_id_int = int(channel_id)
    except (TypeError, ValueError):
        logger.warning("Invalid channel id: %s", channel_id)
        return None

    channel = client.get_channel(channel_id_int)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id_int)
        except Exception:
            logger.exception("Channel lookup failed: %s", channel_id)
            return None
    return channel


async def send_channel_message(
    client: Any,
    channel_id: str | int | None,
    **kwargs: Any,
) -> Any | None:
    """채널에 메시지를 보내고 전송된 메시지를 반환한다. 실패하면 ``None``."""

    channel = await resolve_messageable(client, channel_id)
    if channel is None:
        return None
    try:
        return await channel.send(**kwargs)
    except Exception:
        logger.exception("Channel send failed: channel_id=%s", channel_id)
        return None


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
    except Exception:
        logger.exception(
            "Channel message edit failed: channel_id=%s message_id=%s",
            channel_id,
            message_id,
        )
        return False
    return True
