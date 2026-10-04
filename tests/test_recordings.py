"""Checks against YOUR real recordings (server/recordings/), skipped when there are none.

Only generic properties are asserted here, never values from anyone's account, so this file can be
public while still catching format surprises in freshly captured data.
"""
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from steepserver.config import Config, ServerConfig
from steepserver.constants import CREDITS_ITEM
from steepserver.services import Assets, Services

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
pytestmark = pytest.mark.skipif(not (RECORDINGS / "fixtures.json").exists(), reason="no local recordings")


@pytest.fixture(scope="module")
def real() -> Assets:
    return Assets.load(RECORDINGS)


def test_catalogue_reproduces_every_recorded_offers_query(real):
    families = defaultdict(set)
    for e in real.http.find("GET", r"/v1/spaces/[^/]+/offers$"):
        q = {k: v[0] for k, v in parse_qs(urlsplit(e.path).query).items()}
        key = tuple(sorted((k, v) for k, v in q.items() if k not in ("offset", "limit")))
        families[key] |= {o["offerId"] for o in json.loads(e.body)["offers"]}
    for key, recorded in families.items():
        q = dict(key)
        got = real.catalog.query(tags=q.get("tags"), type=q.get("type"), item_ids=q.get("itemIds"), limit=10**6)
        assert {o["offerId"] for o in got} == recorded, key


def test_recorded_player_seeds_and_stats_round_trip(real, tmp_path):
    s = Services.open(Config(server=ServerConfig(data_dir=tmp_path, recordings_dir=RECORDINGS)), real)
    pid = s.default_player_id()
    s.accounts.player(pid)
    recorded = real.rmc.latest("PlayerStats2Protocol::GetStatisticGroup_V1")
    if recorded:
        assert s.stats.group_reply(pid, "player_profile") == recorded
    shop = next((o for o in real.catalog.by_id.values() if o.get("type") == "shop"), None)
    if shop:
        price = -sum(i["quantity"] for i in shop["items"] if i["itemId"] == CREDITS_ITEM)
        s.economy.grant(pid, CREDITS_ITEM, price)                  # make sure it is affordable
        before = s.economy.credits(pid)
        s.economy.apply_offer(pid, shop["offerId"])
        assert s.economy.credits(pid) == before - price
    s.db.close()
