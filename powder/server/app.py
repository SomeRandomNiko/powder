"""Runs the whole server in one process / one event loop: one TLS port for HTTPS (UbiServices) and
Ubisoft Connect's demux (routed by hostname), UDP (NEX), and the local CA's CRL over HTTP."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from . import log as logsetup
from . import tls
from .config import Config
from .plugins.loader import PluginManager
from .services import Services
from .transport import storm, tls_router
from .transport.demux import server as demux_server
from .transport.https import app as https_app
from .transport.https.keys import CA_PEM
from .transport.nex import secure
from .transport.nex import server as nex_server
from .transport.nex.dispatch import RmcDispatcher

log = logging.getLogger("powder")


def plugins_dir(config: Config) -> Path:
    return (config.path.resolve().parent if config.path else Path.cwd()) / "plugins"


async def run(config: Config) -> None:
    logsetup.setup(config.data_dir / "logs", config.server.log_level)
    cfg = config.server
    services = Services.open(config)
    log.info("data: %s (schema v%d)", config.data_dir, services.db.version())
    a = services.assets
    if not a.empty:                                     # without one, Assets.load said so (built-in replies)
        log.info("game data: %s (built %s: %d offers, %d game replies, %d Ubisoft Connect replies, %d products)",
                 config.gamedata_dir, a.manifest.get("built"), len(a.catalog), len(a.http.entries),
                 len(a.connect_http.entries), len(a.products.products))

    plugins = PluginManager(services)
    plugins.load_dir(plugins_dir(config))

    # The NEX handshake is signed with this key; its public half goes into the rendezvous config.
    signing_key = secure.load_or_create_signing_key(config.data_dir / "nex_signing_key.pem")
    public_key = secure.config_public_key(signing_key)

    app = https_app.build_app(services, plugins, nex_public_key=public_key)
    pki = tls.ensure_pki(config.data_dir / "tls")
    app[CA_PEM] = pki.ca_pem
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    # one TLS port for HTTPS and Ubisoft Connect's demux, routed by hostname
    listener = await tls_router.start(runner, demux_server.handler(services), cfg.bind, cfg.https_port,
                                      pki.server_pem)
    log.info("HTTPS + Ubisoft Connect demux listening on %s:%d (local CA %s)", cfg.bind, cfg.https_port,
             pki.ca_thumbprint)
    crl_runner = None
    try:
        crl_runner = await https_app.start_crl(pki.crl, cfg.bind, cfg.crl_port)
        log.info("CRL on http://%s:%d%s", cfg.bind, cfg.crl_port, tls.CRL_PATH)
    except OSError as e:
        log.warning("cannot serve the CRL on %s:%d (%s): Ubisoft Connect will reject our certificate",
                    cfg.bind, cfg.crl_port, e)

    dispatcher = RmcDispatcher(services, plugins)
    nex, transport = await nex_server.start(services, dispatcher, signing_key, cfg.bind, cfg.public_host,
                                            cfg.nex_port)
    log.info("NEX listening on udp %s:%d (game reaches it at %s)", cfg.bind, cfg.nex_port, cfg.public_host)
    storm_transports = []
    if config.multiplayer.storm == "powder":
        try:
            _, storm_transports = await storm.start(cfg.bind, cfg.storm_port)
            log.info("Storm (peer-to-peer helpers) on udp %s:%d-%d", cfg.bind, cfg.storm_port, cfg.storm_port + 5)
        except OSError as e:
            log.warning("cannot start Storm on udp %s:%d-%d (%s): multiplayer will not find other players",
                        cfg.bind, cfg.storm_port, cfg.storm_port + 5, e)
    try:
        await nex.run_timers()
    finally:
        for t in storm_transports:
            t.close()
        transport.close()
        listener.close()
        await runner.cleanup()
        if crl_runner:
            await crl_runner.cleanup()
        services.db.close()
        log.info("stopped")


def main(config: Config) -> None:
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
