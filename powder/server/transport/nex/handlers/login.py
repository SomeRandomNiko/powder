"""LoginProtocol: the first two NEX calls of every session."""
from __future__ import annotations

import ipaddress
import logging

from ..errors import RmcError
from ..rmc_codec import Reader, Writer, read_type_map

log = logging.getLogger("powder.rmc")


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


async def login_with_token(ctx, r: Reader) -> bytes:
    """params: type map {ClientVersionInfo: 1}, String token, ClientVersionInfo, String.
    The token is the ticket our HTTPS login issued, which ties this connection to that player.
    outputs (reader FUN_7ff661b5bf30): GUID principal (= profile id), String StationURL, bool redirected.
    redirected MUST be false: true makes the client reconnect elsewhere."""
    read_type_map(r)
    token = r.string()
    session = ctx.services.accounts.session_for_ticket(token, ctx.conn.addr[0])
    if session is None:
        raise RmcError("Authentication", 1)
    ctx.conn.session = session
    session.nex = ctx.conn
    await ctx.services.events.emit("nex_connected", session=session)
    log.info("NEX login: %s (%s) from %s:%d", session.player.name, session.profile_id, *ctx.conn.addr)
    return Writer().guid(ctx.profile_guid).string(ctx.station_url).bool(False).bytes()


def register(ctx, r: Reader) -> bytes:
    """params: type map (empty), List<String> = the client's own station URLs (one per NIC).
    outputs (reader FUN_7ff661b5c020): u32 connection id, String public URL = the client's address as
    this server sees it (other keys, e.g. sid, kept from its first URL). The game on the server's own PC
    connects from 127.0.0.1, which other players can't reach: it gets public_host instead (it goes into
    the station URLs the game hands to other players for peer-to-peer)."""
    read_type_map(r)
    urls = [r.string() for _ in range(r.u32())]
    host, port = ctx.conn.addr
    public_host = ctx.services.config.server.public_host
    if is_loopback(host) and not is_loopback(public_host):
        host = public_host
    keys = {"address": host, "port": str(port)}
    if urls and ":/" in urls[0]:
        for kv in urls[0].split(":/", 1)[1].split(";"):
            k, _, v = kv.partition("=")
            if k and k not in keys:
                keys[k] = v
    public = "prudp:/" + ";".join(f"{k}={v}" for k, v in keys.items())
    return Writer().u32(ctx.conn.pid).string(public).bytes()


HANDLERS = {
    "LoginProtocol::LoginWithToken_V1": login_with_token,
    "LoginProtocol::Register_V1": register,
}
