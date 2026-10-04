"""A small, entirely made-up recordings set for the tests (real recordings are personal data and are
never committed). Same file formats as the real recordings directory."""
import base64
import json
from pathlib import Path

from steepserver.constants import APP_ID, CREDITS_ITEM, SPACE_ID
from steepserver.db.repos import StatRow
from steepserver.services.stats import encode_stat_group, encode_value

PLAYER = "00000000-0000-4000-8000-000000000001"
PLAYER_NAME = "TestPlayer"
START_CREDITS = 50_000        # in the inventory snapshot
SEEDED_CREDITS = 50_600       # after the recorded reward update is applied
LEVEL = 41

SKI_A_ITEM = "0000000a-0000-4000-8000-00000000a001"
SKI_B_ITEM = "0000000a-0000-4000-8000-00000000b001"
BADGE_ITEM = "0000000a-0000-4000-8000-00000000c001"
OWNED_ITEM = "0000000a-0000-4000-8000-0000000000ff"

SKI_A = "0000000b-0000-4000-8000-0000000000a1"        # shop: +1 ski, -12000 credits
SKI_B = "0000000b-0000-4000-8000-0000000000b1"        # shop: +1 ski, -30000 credits
REWARD = "0000000b-0000-4000-8000-0000000000c1"       # progression: +600 credits, +1 badge
SKI_A_PRICE = 12_000


def _offer(offer_id, type_, tags, items):
    return {"offerId": offer_id, "spaceId": SPACE_ID, "type": type_, "tags": tags, "revision": 6,
            "nameId": f"TEST_{offer_id[-2:]}", "obj": {}, "public": True,
            "items": [{"itemId": i, "quantity": q, "replacements": [], "destinationSpaceId": SPACE_ID}
                      for i, q in items]}


OFFERS = {
    SKI_A: _offer(SKI_A, "shop", ["STEEP", "SKI", "BASIC"], [(SKI_A_ITEM, 1), (CREDITS_ITEM, -SKI_A_PRICE)]),
    SKI_B: _offer(SKI_B, "shop", ["STEEP", "SKI", "DELUXE"], [(SKI_B_ITEM, 1), (CREDITS_ITEM, -30_000)]),
    REWARD: _offer(REWARD, "progression", ["STEEP", "REWARDS"], [(CREDITS_ITEM, 600), (BADGE_ITEM, 1)]),
}
SKI_PAGE = f"/v1/spaces/{SPACE_ID}/offers?tags=SKI&offset=0&limit=100"
REWARDS_PAGE = f"/v1/spaces/{SPACE_ID}/offers?type=progression&tags=REWARDS&offset=0&limit=100"
CONFIG_PATH = f"/v2/applications/{APP_ID}/configuration"
UBI = "public-ubiservices.ubi.com"


def _http(method, host, path, body, status=200):
    raw = json.dumps(body).encode()
    return {"method": method, "host": host, "path": path, "status": status,
            "content_type": "application/json", "body_b64": base64.b64encode(raw).decode()}


def build(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    login = {"platformType": "uplay", "ticket": "recorded-ticket", "profileId": PLAYER, "userId": PLAYER,
             "nameOnPlatform": PLAYER_NAME, "environment": "Prod", "spaceId": SPACE_ID,
             "sessionKey": "c2Vzc2lvbg==", "rememberMeTicket": None}
    snapshot = {"items": [
        {"itemId": CREDITS_ITEM, "quantity": START_CREDITS, "expirationDate": None,
         "lastModified": "2026-01-01T00:00:00.000Z", "expirationDetails": None},
        {"itemId": OWNED_ITEM, "quantity": 1, "expirationDate": None,
         "lastModified": "2026-01-01T00:00:00.000Z", "expirationDetails": None}]}
    http = [
        _http("POST", UBI, "/v3/profiles/sessions", login),
        _http("GET", UBI, f"/v1/profiles/{PLAYER}/inventory?spaceId={SPACE_ID}", snapshot),
        _http("GET", UBI, SKI_PAGE, {"offers": [OFFERS[SKI_A], OFFERS[SKI_B]]}),
        _http("GET", UBI, REWARDS_PAGE, {"offers": [OFFERS[REWARD]]}),
        _http("GET", UBI, CONFIG_PATH, {"recorded": True}),
    ]
    (folder / "fixtures.json").write_text(json.dumps(http))

    stats = [StatRow("level_stat:6:infinite", *encode_value(LEVEL)),
             StatRow("xp_ratio_stat:0:infinite", *encode_value(0.5))]
    rmc = {
        "PlayerStats2Protocol::GetStatisticGroup_V1": [
            {"params_sha1": "00", "params_len": 0, "body": encode_stat_group(PLAYER, stats).hex()}],
        "MatchmakingProtocol::LeaveSession_V1": [
            {"params_sha1": "01", "params_len": 4, "body": ""},
            {"params_sha1": "02", "params_len": 4, "body": ""}],
    }
    (folder / "fixtures-rmc.json").write_text(json.dumps(rmc))

    updates = [{"time": 1.0, "profileId": PLAYER,
                "request": {"spaceId": SPACE_ID, "offerId": REWARD, "offerRevision": 6},
                "reply": {"items": [
                    {"itemId": CREDITS_ITEM, "quantityBefore": START_CREDITS, "quantity": SEEDED_CREDITS,
                     "lastModified": "2026-01-01T01:00:00.000Z"},
                    {"itemId": BADGE_ITEM, "quantityBefore": 0, "quantity": 1,
                     "lastModified": "2026-01-01T01:00:00.000Z"}],
                    "transactionId": "00000000-0000-4000-8000-0000000000f1"}}]
    (folder / "inventory-updates.json").write_text(json.dumps(updates))
    return folder
