"""Ubisoft Connect's chat channels (channel-service.upc.ubi.com), which also carry game invites.

When Steep invites a friend, Ubisoft Connect gets (or creates) the direct-message channel with that friend
and posts a GAME_INVITE message into it; the friend's Connect learns about both through its notification
websocket (upc_channel_created, upc_channel_message_created) and hands an accepted invite to its game.

Layout from Connect's own UI code (no recordings): a channel has id, type (DM / GROUP), memberships
[{profileId, membershipType ACTIVE / PENDING, addedBy}], lastMessage, lastMessageTime, unreadCount; a
message id, channelId, authorId, type (GAME_INVITE, TEXT_MESSAGE, ...), createTime. What Connect sends
when creating them is logged, and every field it sends is kept in what it gets back.

In memory: channels last until the server stops (invites are only useful while both are playing).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from ..util import iso_now

log = logging.getLogger("powder.channels")

DM, GROUP = "DM", "GROUP"


@dataclass
class Channel:
    data: dict                                          # the channel object as Connect gets it
    messages: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def members(self) -> list[str]:
        return [m.get("profileId", "").lower() for m in self.data.get("memberships", [])]


class ChannelService:
    def __init__(self, push=None):
        self.push = push or (lambda profile_id, notification_type, content: None)
        self.channels: dict[str, Channel] = {}

    def of(self, profile_id: str, space_id: str | None = None) -> list[dict]:
        pid = profile_id.lower()
        return [c.data for c in self.channels.values() if pid in c.members
                and (not space_id or c.data.get("spaceId") in (None, space_id))]

    def create(self, profile_id: str, space_id: str, body: dict) -> dict:
        """POST /v1/spaces/{space}/channels: an existing DM with the same members is returned instead."""
        me = profile_id.lower()
        wanted = {me} | {p.lower() for p in _profile_ids(body)}
        ctype = body.get("type") or (DM if len(wanted) == 2 else GROUP)
        for c in self.channels.values():
            if c.data.get("type") == DM and ctype == DM and set(c.members) == wanted:
                return c.data
        now = iso_now()
        data = {**body, "id": str(uuid.uuid4()), "type": ctype, "spaceId": space_id,
                "memberships": [{"profileId": p, "membershipType": "ACTIVE", "addedBy": me, "joinTime": now}
                                for p in [me, *sorted(wanted - {me})]],
                "createTime": now, "updateTime": now, "lastMessage": None, "lastMessageTime": now, "unreadCount": 0}
        channel = Channel(data)
        self.channels[channel.id] = channel
        log.info("channel %s (%s) created by %s for %s", channel.id, ctype, me, sorted(wanted - {me}))
        for p in wanted - {me}:
            self.push(p, "upc_channel_created", {"channelId": channel.id, "channel": data})
        return data

    def post(self, profile_id: str, channel_id: str, body: dict) -> dict | None:
        """POST .../channels/{id}/messages (a GAME_INVITE when a game invites)."""
        channel = self.channels.get(channel_id)
        me = profile_id.lower()
        if channel is None or me not in channel.members:
            return None
        now = iso_now()
        message = {**body, "id": str(uuid.uuid4()), "channelId": channel_id, "authorId": me,
                   "type": body.get("type") or "TEXT_MESSAGE", "createTime": now}
        channel.messages.insert(0, message)
        channel.data.update(lastMessage=message, lastMessageTime=now)
        log.info("channel %s: %s message from %s", channel_id, message["type"], me)
        for p in channel.members:
            if p != me:
                self.push(p, "upc_channel_message_created", {"channelId": channel_id, "message": message})
        return message

    def messages(self, profile_id: str, channel_id: str) -> list[dict] | None:
        channel = self.channels.get(channel_id)
        if channel is None or profile_id.lower() not in channel.members:
            return None
        return channel.messages


def _profile_ids(body: dict) -> list[str]:
    """The other members named in a create request, wherever Connect puts them."""
    out = []
    for key in ("profileIds", "memberIds", "members", "memberships", "invitees", "participants"):
        for v in body.get(key) or []:
            pid = v.get("profileId") if isinstance(v, dict) else v
            if isinstance(pid, str):
                out.append(pid)
    for key in ("profileId", "friendProfileId", "recipientId"):
        if isinstance(body.get(key), str):
            out.append(body[key])
    return out
