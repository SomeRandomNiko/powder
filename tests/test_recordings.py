"""Checks against YOUR real recordings (server/recordings/), skipped when there are none.

Only generic properties are asserted here, never values from anyone's account, so this file can be
public while still catching format surprises in freshly captured data.
"""
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from powder.server.config import Config, ServerConfig
from powder.server.constants import CREDITS_ITEM
from powder.server.fixtures.http import HttpFixtures
from powder.server.fixtures.rmc import RmcFixtures
from powder.server.gamedata.build import build
from powder.server.services import Assets, Services
from powder.server.services.importer import import_recordings

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
pytestmark = pytest.mark.skipif(not (RECORDINGS / "fixtures.json").exists(), reason="no local recordings")


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> Assets:
    """A game-data pack built from the real recordings; the build includes the privacy check."""
    out = tmp_path_factory.mktemp("pack") / "gamedata"
    report = build(RECORDINGS, out)
    assert not report.findings, f"privacy check failed: {report.findings[:5]}"
    return Assets.load(out)


def test_pack_catalogue_reproduces_every_recorded_offers_query(real):
    families = defaultdict(set)
    for e in HttpFixtures.load(RECORDINGS / "fixtures.json").find("GET", r"/v1/spaces/[^/]+/offers$"):
        q = {k: v[0] for k, v in parse_qs(urlsplit(e.path).query).items()}
        key = tuple(sorted((k, v) for k, v in q.items() if k not in ("offset", "limit")))
        families[key] |= {o["offerId"] for o in json.loads(e.body)["offers"]}
    for key, recorded in families.items():
        q = dict(key)
        got = real.catalog.query(tags=q.get("tags"), type=q.get("type"), item_ids=q.get("itemIds"), limit=10**6)
        assert {o["offerId"] for o in got} == recorded, key


def _session_timeline():
    """Stat snapshots and stat uploads from recordings/sessions/*.jsonl (decrypted NEX captures)."""
    from powder.server.services.stats import decode_stat_group
    from powder.server.transport.nex.handlers.stats import decode_events
    from powder.server.transport.nex.rmc_codec import Reader
    timeline = []
    for f in sorted((RECORDINGS / "sessions").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = json.loads(line)
            method = (m.get("method") or "").rstrip("*")
            body = bytes.fromhex(m.get("body") or "")
            if method == "PlayerStats2Protocol::GetStatisticGroup_V1" and not m["is_request"] and m["success"]:
                timeline.append((m["time"], "snapshot", decode_stat_group(body)[1]))
            elif method.startswith("PlayerStats2Protocol::PostEvent") and m["is_request"]:
                timeline.append((m["time"], "events", decode_events(Reader(body), method.endswith("PostEvent_V1"))))
    return sorted(timeline, key=lambda x: x[0])


@pytest.mark.skipif(not list((RECORDINGS / "sessions").glob("*.jsonl")), reason="no recorded sessions")
def test_event_rules_reproduce_every_recorded_stat_change(tmp_path):
    """Start from each recorded stat snapshot, apply the events the game uploaded until the next
    snapshot, and require the result to equal that next snapshot exactly."""
    timeline = _session_timeline()
    snapshots = [i for i, t in enumerate(timeline) if t[1] == "snapshot"]
    assert len(snapshots) >= 2
    pid = "00000000-0000-4000-8000-0000000000aa"
    s = Services.open(Config(server=ServerConfig(data_dir=tmp_path, gamedata_dir=tmp_path / "none")))
    s.repos.players.create(pid, pid, "replay")
    for a, b in zip(snapshots, snapshots[1:]):
        s.repos.stats.clear(pid)
        for row in timeline[a][2]:
            s.repos.stats.set(pid, "player_profile", row.key, row.type, row.value)
        for _, kind, events in timeline[a + 1:b]:
            s.stats.apply_events(pid, events)
        got = {r.key: (r.type, r.value) for r in s.stats.group(pid, "player_profile")}
        want = {r.key: (r.type, r.value) for r in timeline[b][2]}
        diff = {k: (got.get(k), want.get(k)) for k in set(got) | set(want) if got.get(k) != want.get(k)}
        assert not diff, f"snapshot {a} -> {b}: {diff}"
    s.db.close()


def test_import_and_stats_round_trip(real, tmp_path):
    s = Services.open(Config(server=ServerConfig(data_dir=tmp_path)), real)
    pid = import_recordings(s, RECORDINGS).profile_id
    assert s.default_player_id() == pid
    recorded = RmcFixtures.load(RECORDINGS / "fixtures-rmc.json").latest("PlayerStats2Protocol::GetStatisticGroup_V1")
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
