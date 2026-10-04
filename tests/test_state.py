"""Catalogue, seeding, stats and economy (against the synthetic recordings)."""
import pytest

import synthetic as syn
from steepserver.constants import CREDITS_ITEM
from steepserver.services import Services
from steepserver.services.economy import EconomyError
from steepserver.services.stats import LEVEL_KEY


def test_catalog_filters(assets):
    ids = lambda offers: {o["offerId"] for o in offers}
    assert ids(assets.catalog.query(tags="SKI")) == {syn.SKI_A, syn.SKI_B}
    assert ids(assets.catalog.query(tags="SKI,BASIC")) == {syn.SKI_A}             # AND
    assert ids(assets.catalog.query(type="progression")) == {syn.REWARD}
    assert ids(assets.catalog.query(item_ids=syn.SKI_B_ITEM)) == {syn.SKI_B}        # grants any of
    assert len(assets.catalog.query(tags="SKI", offset=1, limit=1)) == 1


def test_default_player_is_the_recorded_account(services):
    assert services.default_player_id() == syn.PLAYER


def test_seed_from_recordings(services, player):
    assert services.repos.players.get(player).name == syn.PLAYER_NAME
    assert services.stats.get(player, LEVEL_KEY) == syn.LEVEL
    assert services.economy.credits(player) == syn.SEEDED_CREDITS                  # snapshot + update
    assert services.repos.inventory.quantity(player, syn.BADGE_ITEM) == 1


def test_stat_group_reply_is_byte_identical_to_recording(services, player, assets):
    recorded = assets.rmc.latest("PlayerStats2Protocol::GetStatisticGroup_V1")
    assert services.stats.group_reply(player, "player_profile") == recorded


def test_purchase_deducts_credits_adds_item_and_persists(make_services, config):
    s = make_services()
    s.accounts.player(syn.PLAYER)
    reply, _ = s.economy.apply_offer(syn.PLAYER, syn.SKI_A, 6)
    credits = next(i for i in reply["items"] if i["itemId"] == CREDITS_ITEM)
    after = syn.SEEDED_CREDITS - syn.SKI_A_PRICE
    assert (credits["quantityBefore"], credits["quantity"]) == (syn.SEEDED_CREDITS, after)
    assert {d["receivedQuantity"] for d in reply["additionalDetails"]} == {1, -syn.SKI_A_PRICE}
    inv = {i["itemId"]: i["quantity"] for i in s.economy.inventory_reply(syn.PLAYER)["items"]}
    assert inv[CREDITS_ITEM] == after and inv[syn.SKI_A_ITEM] == 1
    s.db.close()

    reopened = Services.open(config, s.assets)                  # same database file
    assert reopened.economy.credits(syn.PLAYER) == after
    assert reopened.repos.ledger.recent(syn.PLAYER, 1)[0]["reason"] == "purchase"
    reopened.db.close()


def test_reward_adds_credits(services, player):
    services.economy.apply_offer(player, syn.REWARD)
    assert services.economy.credits(player) == syn.SEEDED_CREDITS + 600


def test_not_enough_credits_is_refused_and_changes_nothing(services, player):
    services.economy.grant(player, CREDITS_ITEM, -syn.SEEDED_CREDITS + 100)
    with pytest.raises(EconomyError) as e:
        services.economy.apply_offer(player, syn.SKI_A)
    assert e.value.status == 400
    assert services.economy.credits(player) == 100
    assert services.repos.inventory.quantity(player, syn.SKI_A_ITEM) == 0


def test_unknown_offer_is_404(services, player):
    with pytest.raises(EconomyError) as e:
        services.economy.apply_offer(player, "ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert e.value.status == 404


def test_toggles_free_purchases_and_reward_multiplier(make_services):
    s = make_services(free_purchases=True, reward_credit_multiplier=2.0)
    s.accounts.player(syn.PLAYER)
    s.economy.apply_offer(syn.PLAYER, syn.SKI_A)
    assert s.economy.credits(syn.PLAYER) == syn.SEEDED_CREDITS
    s.economy.apply_offer(syn.PLAYER, syn.REWARD)
    assert s.economy.credits(syn.PLAYER) == syn.SEEDED_CREDITS + 1200


def test_new_player_starts_empty_with_starting_credits(make_services):
    s = make_services(starting_credits=5000)
    p = s.accounts.player("11111111-2222-4333-8444-555555555555")
    assert s.economy.credits(p.profile_id) == 5000
    assert s.stats.group(p.profile_id, "player_profile") == []


def test_without_recordings_a_local_default_player_is_created_once(tmp_path):
    from steepserver.config import Config, ServerConfig
    cfg = Config(server=ServerConfig(data_dir=tmp_path / "d", recordings_dir=tmp_path / "none"))
    s = Services.open(cfg)
    first = s.default_player_id()
    assert s.assets.empty and s.default_player_id() == first
    s.db.close()
    assert Services.open(cfg).default_player_id() == first       # kept across restarts


def test_reseed_restores_recorded_state(services, player):
    services.economy.apply_offer(player, syn.SKI_A)
    services.reseed(player)
    assert services.economy.credits(player) == syn.SEEDED_CREDITS
