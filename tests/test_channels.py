"""Ubisoft Connect's chat channels, which carry game invites (services/channels.py). Made-up ids."""
from powder.server.services.channels import ChannelService

A, B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
SPACE = "c0539e9c-0000-0000-0000-000000000000"


def make():
    pushed = []
    return ChannelService(lambda pid, kind, content: pushed.append((pid, kind, content))), pushed


def test_a_game_invite_reaches_the_friend():
    channels, pushed = make()
    dm = channels.create(A, SPACE, {"channel": {"type": "DM"}, "members": [B]})          # as Connect sends it
    assert dm["type"] == "DM" and {m["profileId"] for m in dm["memberships"]} == {A, B}
    assert all(m["membershipType"] == "ACTIVE" for m in dm["memberships"])
    assert pushed == [(B, "upc_channel_created", {"channelId": dm["id"], "channel": dm})]
    assert channels.create(A, SPACE, {"channel": {"type": "DM"}, "members": [B.upper()]})["id"] == dm["id"]
    invite = "eyJnYW1lU2Vzc2lvbklkIjoxMjN9"                                           # {"gameSessionId":123}
    msg = channels.post(A, dm["id"], {"type": "GAME_INVITE", "textMessage": invite})
    assert msg["authorId"] == A and msg["type"] == "GAME_INVITE" and msg["textMessage"] == invite
    assert pushed[-1] == (B, "upc_channel_message_created", {"channelId": dm["id"], "message": msg})
    assert channels.of(B, SPACE) == [dm] and dm["lastMessage"] == msg
    assert channels.messages(B, dm["id"]) == [msg]


def test_strangers_see_nothing():
    channels, _ = make()
    dm = channels.create(A, SPACE, {"channel": {"type": "DM"}, "members": [B]})
    other = "33333333-3333-3333-3333-333333333333"
    assert channels.of(other) == [] and channels.messages(other, dm["id"]) is None
    assert channels.post(other, dm["id"], {"type": "TEXT_MESSAGE"}) is None
