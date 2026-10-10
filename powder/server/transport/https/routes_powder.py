"""The server's own endpoints (not Ubisoft's), under /_powder/. Used by the launcher; the web admin will
build on them. The status needs no sign-in and shows player names and the address of every connected game,
which is fine for a server meant for a local network of friends; don't expose it to the internet. The
account calls carry the account's email and password.

  GET /_powder/status   who is connected: Ubisoft Connect (demux) clients and game (NEX) sessions, and
                        the caller's address as the server sees it ("you": a launcher finds its own PC)
  GET /_powder/account/new        whether registering takes over the existing player
  POST /_powder/account/register|login|email|password|name   accounts, for the launcher (JSON; each
                        call carries email and password, failed ones are rate-limited per address)
  GET /_powder/ca.pem   the local CA certificate (public part), for clients that must trust it
  GET /_powder/overlay/powder.js   the Powder part of Ubisoft Connect's in-game overlay
"""
from __future__ import annotations

from aiohttp import web

from ....version import __version__
from ...builtin import connect_ui
from ...services.accounts import AccountError, AuthError
from .keys import CA_PEM, HOW, SERVICES
from .routes_ubi import client_ip


REGISTER_KEY = "#register"


def _peer(nex) -> str:
    addr = getattr(nex, "addr", None)
    return f"{addr[0]}:{addr[1]}" if addr else ""


async def status(request: web.Request) -> web.Response:
    services = request.app[SERVICES]
    connect = [{"player": c.player_name, "peer": c.peer, "services": sorted(set(c.channels.values()))}
               for c in services.connect_clients if c.profile_id]
    game = [{"player": s.player.name, "nex": s.nex is not None, "peer": _peer(s.nex)}
            for s in services.sessions.by_player.values() if s.nex is not None]
    request[HOW] = "powder-status"
    return web.json_response({"server": "powder", "version": __version__, "you": client_ip(request),
                              "public_host": services.config.server.public_host,
                              "accounts": services.accounts.required,
                              "players": len(services.repos.players.all()),
                              "gamedata": not services.assets.empty,
                              "connect": connect, "game": game})


async def ca_pem(request: web.Request) -> web.StreamResponse:
    path = request.app.get(CA_PEM)
    request[HOW] = "powder-ca"
    if path is None or not path.exists():
        return web.Response(status=404, text="no CA")
    return web.Response(body=path.read_bytes(), content_type="application/x-pem-file")


async def overlay_part(request: web.Request) -> web.Response:
    """The Powder part of Ubisoft Connect's in-game overlay (builtin/connect_ui.py); the overlay loads it
    cross-origin, like Ubisoft's parts from their CDN."""
    request[HOW] = "powder-overlay"
    return web.Response(text=connect_ui.POWDER_PART_JS, content_type="application/javascript",
                        headers={"Access-Control-Allow-Origin": "*"})


# ---- accounts (the launcher registers and manages them) ---------------------------------------
# JSON bodies; every call that reads or changes an account carries its email and current password, so
# the launcher needs no session of its own. Bodies are never logged (passwords).
async def _account_call(request: web.Request, change) -> web.Response:
    services = request.app[SERVICES]
    limiter = services.login_failures
    ip = client_ip(request)
    request[HOW] = "powder-account"
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return web.json_response({"error": "bad request"}, status=400)
    email = str(body.get("email", ""))
    key = REGISTER_KEY if change is None else email      # registrations count like failures (hashing is slow)
    if limiter.blocked(ip, key):
        return web.json_response({"error": "Too many attempts, try again in a few minutes."}, status=429)
    accounts = services.accounts
    try:
        if change is None:
            limiter.failed(ip, REGISTER_KEY)
            account = accounts.register(email, str(body.get("password", "")), str(body.get("name", "")))
        else:
            account = accounts.authenticate(email, str(body.get("password", "")))
            limiter.succeeded(ip, email)
            account = change(accounts, account, body) or account
    except AuthError:
        limiter.failed(ip, email)
        return web.json_response({"error": "Wrong email or password."}, status=401)
    except AccountError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(accounts.info(account))


async def account_register(request: web.Request) -> web.Response:
    return await _account_call(request, None)


async def account_login(request: web.Request) -> web.Response:
    return await _account_call(request, lambda accounts, account, body: None)


async def account_email(request: web.Request) -> web.Response:
    return await _account_call(request, lambda accounts, account, body:
                               accounts.change_email(account, str(body.get("new_email", ""))))


async def account_password(request: web.Request) -> web.Response:
    return await _account_call(request, lambda accounts, account, body:
                               accounts.change_password(account, str(body.get("new_password", ""))))


async def account_name(request: web.Request) -> web.Response:
    return await _account_call(request, lambda accounts, account, body:
                               accounts.rename(account, str(body.get("name", ""))))


async def account_new(request: web.Request) -> web.Response:
    """What registering would do here: take over an existing player (the first account on a server)."""
    request[HOW] = "powder-account"
    return web.json_response({"takes_over": request.app[SERVICES].accounts.claimable_player_name()})


def add_routes(app: web.Application) -> None:
    app.router.add_get("/_powder/status", status)
    app.router.add_get("/_powder/account/new", account_new)
    app.router.add_post("/_powder/account/register", account_register)
    app.router.add_post("/_powder/account/login", account_login)
    app.router.add_post("/_powder/account/email", account_email)
    app.router.add_post("/_powder/account/password", account_password)
    app.router.add_post("/_powder/account/name", account_name)
    app.router.add_get("/_powder/ca.pem", ca_pem)
    app.router.add_get(connect_ui.POWDER_PART_PATH, overlay_part)
