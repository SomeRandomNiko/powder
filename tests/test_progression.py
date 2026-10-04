"""Phase 2: stat uploads applied to the player's stats, challenge scores, profile entities."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from steepserver.config import Config, ServerConfig
from steepserver.db.database import MIGRATIONS_DIR, Database, _split_sql
from steepserver.services import Services
from steepserver.services.stats import LEVEL_KEY, StatEvent
from steepserver.transport.https.app import build_app
from steepserver.transport.nex.handlers.stats import decode_events
from steepserver.transport.nex.rmc_codec import (V_BOOL, V_DOUBLE, V_INT64, V_STRING, V_UINT64, Reader,
                                                 Writer, read_variant, write_variant)


def encode_events(events: list[tuple[str, dict]], single=False) -> bytes:
    """Build PostEvents/PostEvent params the way the game does."""
    w = Writer().u32(1).string("StatisticEvent").u16(1)
    if not single:
        w.u32(len(events))
    for name, fields in events:
        w.string(name).u32(len(fields))
        for key, (t, v) in fields.items():
            write_variant(w.string(key), t, v)
    return w.bytes()


def ev(name, **fields) -> StatEvent:
    return StatEvent(name, fields)


def test_variant_round_trip():
    for t, v in [(V_INT64, -5), (V_DOUBLE, 0.25), (V_BOOL, True), (V_STRING, "abc"), (V_UINT64, 2**63 + 1)]:
        assert read_variant(Reader(write_variant(Writer(), t, v).bytes())) == (t, v)


def test_decode_events_multi_and_single():
    body = encode_events([("update_xp", {"expertise_id": (V_UINT64, 2), "xp_earned": (V_UINT64, 500),
                                         "xp_ratio_expertise": (V_DOUBLE, 0.5)})])
    (e,) = decode_events(Reader(body), single=False)
    assert e.name == "update_xp" and e.plain() == {"expertise_id": 2, "xp_earned": 500, "xp_ratio_expertise": 0.5}
    body = encode_events([("descending_public_challenge_score_event",
                           {"challenge_id_to_use": (V_STRING, "C1"), "score": (V_STRING, "100")})], single=True)
    assert decode_events(Reader(body), single=True)[0].plain()["score"] == "100"


def test_rules(services, player):
    st = services.stats
    st.apply_events(player, [
        ev("update_level", expertise_id=(V_UINT64, 6), level_expertise=(V_UINT64, 42)),
        ev("update_xp", expertise_id=(V_UINT64, 6), xp_earned=(V_UINT64, 9), xp_ratio_expertise=(V_DOUBLE, 0.125)),
        ev("update_player_playtime", playtime=(V_UINT64, 5_000)),
        ev("complete_memorable_repeatable", memorable_id=(V_UINT64, 7), count=(V_INT64, 1)),
        ev("complete_memorable_repeatable", memorable_id=(V_UINT64, 7), count=(V_INT64, 2)),
        ev("unlock_exploration", type_id=(V_UINT64, 2), mapping_id=(V_UINT64, 1), bitfield=(V_INT64, 0b0101)),
        ev("unlock_exploration", type_id=(V_UINT64, 2), mapping_id=(V_UINT64, 1), bitfield=(V_INT64, 0b0011)),
        ev("unlock_challenge", mapping_id=(V_UINT64, 3), bitfield=(V_INT64, -(1 << 63))),   # top bit
    ])
    assert st.get(player, LEVEL_KEY) == 42
    assert st.get(player, "xp_ratio_stat:6:infinite") == 0.125
    assert st.get(player, "player_playtime:infinite") == 5_000
    assert st.get(player, "memorable_repeatable:7:infinite") == 3
    assert st.get(player, "unlockable_exploration:2:1:infinite") == 0b0111
    assert st.get(player, "unlockable_challenge:3:infinite") == -(1 << 63)
    assert len(services.repos.stat_events.recent(player, 100)) == 8


def test_stale_backlog_never_regresses(services, player):
    """A game that was given stale stats uploads low levels / zero playtime: nothing must go down."""
    st = services.stats
    st.apply_events(player, [ev("update_player_playtime", playtime=(V_UINT64, 9_000))])
    st.apply_events(player, [
        ev("update_level", expertise_id=(V_UINT64, 6), level_expertise=(V_UINT64, 2)),
        ev("update_xp", expertise_id=(V_UINT64, 6), xp_earned=(V_UINT64, 0), xp_ratio_expertise=(V_DOUBLE, 0.9)),
        ev("update_player_playtime", playtime=(V_UINT64, 0)),
    ])
    assert st.get(player, LEVEL_KEY) == syn.LEVEL
    assert st.get(player, "xp_ratio_stat:6:infinite") is None        # stale ratio skipped, not written
    assert st.get(player, "player_playtime:infinite") == 9_000


def test_unknown_event_is_stored_not_applied(services, player):
    before = services.stats.group(player, "player_profile")
    assert services.stats.apply_events(player, [ev("brand_new_event", x=(V_INT64, 1))]) == 0
    assert services.stats.group(player, "player_profile") == before
    assert services.repos.stat_events.recent(player, 1)[0]["name"] == "brand_new_event"


def test_challenge_personal_best(services, player):
    def score(kind, value):
        services.stats.apply_events(player, [ev(f"{kind}_public_challenge_score_event",
                                                challenge_id_to_use=(V_STRING, f"C_{kind}"),
                                                replay=(V_STRING, "none"), score=(V_STRING, str(value)))])
    for v in (100, 300, 200):
        score("descending", v)                   # higher is better
    for v in (50, 30, 40):
        score("ascending", v)                    # lower is better
    best = {s["challenge_id"]: s["best_score"] for s in services.repos.challenge_scores.all(player)}
    assert best == {"C_descending": 300, "C_ascending": 30}


def test_existing_phase1_database_is_migrated(tmp_path):
    path = tmp_path / "data" / "steep.db"
    db = Database(path)
    for stmt in _split_sql((MIGRATIONS_DIR / "0001_init.sql").read_text()):   # a Phase 1 database
        db.execute(stmt)
    db.execute("PRAGMA user_version = 1")
    db.execute("INSERT INTO players VALUES ('p1', 'p1', 'Old', 'now', '{}')")
    db.close()
    s = Services.open(Config(server=ServerConfig(data_dir=tmp_path / "data", recordings_dir=tmp_path / "none")))
    assert s.db.version() >= 2 and s.repos.players.get("p1").name == "Old"
    s.db.close()


async def test_entities_over_https(services, player):
    client = TestClient(TestServer(build_app(services)))
    await client.start_server()
    try:
        hdr = {"Host": syn.UBI}
        login = await (await client.post("/v3/profiles/sessions", headers={**hdr, "Authorization": "uplaypc_v1 t=x"},
                                         json={})).json()
        auth = {**hdr, "Authorization": f"Ubi_v1 t={login['ticket']}"}

        # own rider card: seeded from the recording, then changed by the game and read back
        own = (await (await client.get(f"/v2/profiles/{player}/entities?type=PROFILE", headers=auth)).json())["entities"]
        assert [e["entityId"] for e in own] == [syn.CARD]
        r = await client.put(f"/v2/profiles/entities/{syn.CARD}", headers=auth,
                             json={"type": "PROFILE", "obj": {"RIDER_CARD": {"title": 99}}})
        saved = await r.json()
        assert saved["revision"] == 4 and saved["obj"]["RIDER_CARD"]["title"] == 99 and saved["profileId"] == player

        # batch: own card from the DB + friend's from the recordings, for a combination never recorded
        r = await client.get(f"/v2/profiles/entities?profileIds={player},{syn.FRIEND}&type=PROFILE", headers=auth)
        cards = {e["profileId"]: e["obj"]["RIDER_CARD"]["title"] for e in (await r.json())["entities"]}
        assert cards == {player: 99, syn.FRIEND: 2}

        # favorites: create and delete
        fav = await (await client.post("/v2/profiles/entities", headers=auth,
                                       json={"type": "FAVORITE_CHALLENGE", "tags": ["TRAIL"], "obj": {"c": 1}})).json()
        favs = (await (await client.get(f"/v2/profiles/{player}/entities?type=FAVORITE_CHALLENGE&tags=TRAIL",
                                        headers=auth)).json())["entities"]
        assert [f["entityId"] for f in favs] == [fav["entityId"]]
        await client.delete(f"/v2/profiles/entities/{fav['entityId']}", headers=auth)
        assert services.repos.entities.get(fav["entityId"]) is None

        # populations data is kept, reply as recorded
        r = await client.put("/v1/profiles/me/populations/data", headers=auth, json={"populations": [1]})
        assert await r.json() == {"recorded": "populations"}
        assert services.repos.kv.get(player, "populations_data") == {"populations": [1]}
    finally:
        await client.close()


def test_entities_persist_and_survive_reseed_reset(make_services, config):
    s = make_services()
    s.accounts.player(syn.PLAYER)
    s.entities.put(syn.CARD, {"obj": {"RIDER_CARD": {"title": 5}}}, syn.PLAYER)
    s.db.close()
    s2 = Services.open(config, s.assets)
    assert s2.entities.get(syn.CARD)["obj"]["RIDER_CARD"]["title"] == 5
    s2.reseed(syn.PLAYER)
    assert s2.entities.for_profile(syn.PLAYER)[0]["obj"]["RIDER_CARD"]["title"] == 1     # recorded again
    s2.db.close()
