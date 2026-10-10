"""Accounts: registered in the launcher (/_powder/account/*), signed in with in Ubisoft Connect (Basic,
then rm_v1), and the game following the player Connect signed in as on the same PC."""
import base64
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from powder.server.core.identity import LoginRequest
from powder.server.core.ratelimit import FailureLimiter
from powder.server.services.accounts import AccountError, AuthError, check_password, hash_password
from powder.server.transport.https.app import build_app

CONNECT_APP = "f68a4bb5-608a-4ff2-8123-be8ef797e0a6"
PASSWORD = "correct horse"
UBI = {"Host": syn.UBI}


def basic(email: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{password}".encode()).decode()


@pytest.fixture
async def client(services, player):            # the made-up player, imported: the default player
    c = TestClient(TestServer(build_app(services, nex_public_key="TESTKEY")))
    await c.start_server()
    yield c
    await c.close()


async def connect_login(client, auth: str):
    return await client.post("/v3/profiles/sessions", json={"rememberMe": True},
                             headers={"Ubi-AppId": CONNECT_APP, "Authorization": auth})


async def game_login(client):
    return await client.post("/v3/profiles/sessions", json={},
                             headers={**UBI, "Authorization": "uplaypc_v1 t=connect-made-ticket"})


async def register(client, email="rider@example.com", password=PASSWORD, name="Rider"):
    return await client.post("/_powder/account/register", json={"email": email, "password": password, "name": name})


def test_password_hash():
    h = hash_password(PASSWORD)
    assert PASSWORD not in h and check_password(PASSWORD, h)
    assert not check_password("wrong", h) and not check_password(PASSWORD, "garbage")
    assert hash_password(PASSWORD) != h                     # salted


def test_first_account_takes_over_the_default_player(services, player):
    assert services.accounts.claimable_player_name() == syn.PLAYER_NAME
    first = services.accounts.register("Host@Example.com", PASSWORD, "")
    assert first.profile_id == syn.PLAYER                  # keeps the imported progress
    assert services.accounts.claimable_player_name() is None
    second = services.accounts.register("guest@example.com", PASSWORD, "Guest")
    assert second.profile_id != syn.PLAYER and services.repos.players.get(second.profile_id).name == "Guest"


def test_register_refuses(services, player):
    services.accounts.register("host@example.com", PASSWORD, "")
    for email, password, name in [("HOST@example.com", PASSWORD, "Other"),     # email taken (any case)
                                  ("not-an-email", PASSWORD, "Other"),
                                  ("new@example.com", "short", "Other"),
                                  ("new@example.com", PASSWORD, syn.PLAYER_NAME.upper()),   # name taken
                                  ("new@example.com", PASSWORD, "x")]:                      # name too short
        with pytest.raises(AccountError):
            services.accounts.register(email, password, name)


def test_change_email_and_password(services, player):
    acc = services.accounts.register("host@example.com", PASSWORD, "")
    acc = services.accounts.change_email(acc, "new@example.com")
    with pytest.raises(AuthError):
        services.accounts.authenticate("host@example.com", PASSWORD)
    assert services.accounts.authenticate("NEW@example.com", PASSWORD).account_id == acc.account_id
    services.repos.remember_tickets.add("rm-ticket", acc.account_id)
    services.accounts.change_password(acc, "another password")
    assert services.repos.remember_tickets.account("rm-ticket") is None      # Connect must sign in again
    with pytest.raises(AuthError):
        services.accounts.authenticate("new@example.com", PASSWORD)
    services.accounts.authenticate("new@example.com", "another password")


async def test_launcher_account_api(client, services):
    r = await client.get("/_powder/account/new")
    assert (await r.json())["takes_over"] == syn.PLAYER_NAME
    r = await register(client, name="")
    assert r.status == 200 and (await r.json())["profile_id"] == syn.PLAYER
    r = await register(client, email="RIDER@example.com", name="Other")
    assert r.status == 400 and "already exists" in (await r.json())["error"]
    r = await client.post("/_powder/account/login", json={"email": "rider@example.com", "password": "nope"})
    assert r.status == 401
    r = await client.post("/_powder/account/name", json={"email": "rider@example.com", "password": PASSWORD,
                                                         "name": "NewName"})
    assert r.status == 200 and (await r.json())["name"] == "NewName"
    r = await client.post("/_powder/account/email", json={"email": "rider@example.com", "password": PASSWORD,
                                                          "new_email": "moved@example.com"})
    assert (await r.json())["email"] == "moved@example.com"
    r = await client.post("/_powder/account/password", json={"email": "moved@example.com", "password": PASSWORD,
                                                             "new_password": "x"})
    assert r.status == 400


async def test_connect_signs_in_with_email_and_password_then_remember_me(client, services):
    await register(client)
    r = await connect_login(client, basic("rider@example.com", "wrong password"))
    assert r.status == 401 and (await r.json())["errorCode"] == 1
    r = await connect_login(client, basic("rider@example.com", PASSWORD))
    assert r.status == 200
    body = await r.json()
    assert body["profileId"] == syn.PLAYER
    r = await connect_login(client, f"rm_v1 t={body['rememberMeTicket']}")
    assert r.status == 200
    services.sessions.by_ticket.clear()                     # a server restart: remember-me still works
    services.sessions.by_player.clear()
    assert (await connect_login(client, f"rm_v1 t={body['rememberMeTicket']}")).status == 200
    assert (await connect_login(client, "rm_v1 t=made-up")).status == 401


async def test_game_follows_connect_on_the_same_pc(client, services):
    await register(client)
    assert (await game_login(client)).status == 401          # nobody signed in to Connect here yet
    await connect_login(client, basic("rider@example.com", PASSWORD))
    r = await game_login(client)
    assert r.status == 200 and (await r.json())["profileId"] == syn.PLAYER


def test_game_login_from_two_pcs(services, player):
    a = services.accounts.register("a@example.com", PASSWORD, "")
    b = services.accounts.register("b@example.com", PASSWORD, "PlayerB")
    for email, ip in (("a@example.com", "127.0.0.1"), ("b@example.com", "192.0.2.20")):
        services.accounts.login(LoginRequest(ip, "Basic", basic(email, PASSWORD).split()[1],
                                             {"Ubi-AppId": CONNECT_APP}))
    assert services.accounts.login(LoginRequest("127.0.0.1", "uplaypc_v1", "x")).profile_id == a.profile_id
    assert services.accounts.login(LoginRequest("192.0.2.20", "uplaypc_v1", "x")).profile_id == b.profile_id
    with pytest.raises(AuthError):
        services.accounts.login(LoginRequest("192.0.2.30", "uplaypc_v1", "x"))


async def test_without_accounts_every_credential_still_works(client, services):
    """A server from before accounts: until the first registration, logins work as they did."""
    assert (await connect_login(client, basic("anyone@example.com", "anything"))).status == 200
    assert (await game_login(client)).status == 200


async def test_failed_sign_ins_are_rate_limited(client, services):
    await register(client)
    services.login_failures.max_failures = 3
    for _ in range(3):
        assert (await connect_login(client, basic("rider@example.com", "wrong password"))).status == 401
    r = await connect_login(client, basic("rider@example.com", PASSWORD))
    assert r.status == 401 and "Too many" in (await r.json())["message"]


def test_failure_limiter_window():
    now = [0.0]
    lim = FailureLimiter(max_failures=2, window_s=10, clock=lambda: now[0])
    lim.failed("a"), lim.failed("a")
    assert lim.blocked("a") and not lim.blocked("b")
    now[0] = 11
    assert not lim.blocked("a")


async def test_passwords_never_logged(client, services, caplog):
    caplog.set_level(logging.DEBUG)
    await register(client)
    await connect_login(client, basic("rider@example.com", PASSWORD))
    await connect_login(client, basic("rider@example.com", "wrong password"))
    await client.post("/_powder/account/password", json={"email": "rider@example.com", "password": PASSWORD,
                                                         "new_password": "brand new secret"})
    assert PASSWORD not in caplog.text and "wrong password" not in caplog.text
    assert "brand new secret" not in caplog.text


def test_resetting_a_player_keeps_its_account(services, player):
    acc = services.accounts.register("host@example.com", PASSWORD, "")
    services.reset(acc.profile_id)
    assert services.accounts.authenticate("host@example.com", PASSWORD).profile_id == acc.profile_id
    assert services.repos.players.get(acc.profile_id).name == syn.PLAYER_NAME and services.accounts.required


async def test_signing_in_to_ones_own_account_does_not_reset_the_guess_count(client, services):
    await register(client)
    await register(client, email="attacker@example.com", name="Attacker")
    services.login_failures.max_failures = 3
    for _ in range(2):
        await connect_login(client, basic("rider@example.com", "wrong password"))
        assert (await connect_login(client, basic("attacker@example.com", PASSWORD))).status == 200
    await connect_login(client, basic("rider@example.com", "wrong password"))
    r = await connect_login(client, basic("rider@example.com", PASSWORD))
    assert r.status == 401 and "Too many" in (await r.json())["message"]


async def test_empty_game_credentials_are_not_counted_as_guesses(client, services):
    await register(client)
    services.login_failures.max_failures = 2
    empty = "Basic " + base64.b64encode(b":").decode()
    for _ in range(5):                                       # the game without a Connect session
        r = await client.post("/v3/profiles/sessions", json={}, headers={**UBI, "Authorization": empty})
        assert r.status == 401
    assert (await connect_login(client, basic("rider@example.com", PASSWORD))).status == 200
