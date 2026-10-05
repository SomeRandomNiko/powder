"""Runs the whole server in one process / one event loop: HTTPS (UbiServices) + UDP (NEX)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import log as logsetup
from . import tls
from .config import Config
from .plugins.loader import PluginManager
from .services import Services
from .transport.https import app as https_app
from .transport.nex import secure
from .transport.nex import server as nex_server
from .transport.nex.dispatch import RmcDispatcher

log = logging.getLogger("steep")


def plugins_dir(config: Config) -> Path:
    return (config.path.resolve().parent if config.path else Path.cwd()) / "plugins"


async def run(config: Config) -> None:
    logsetup.setup(config.data_dir / "logs", config.server.log_level)
    cfg = config.server
    services = Services.open(config)
    log.info("data: %s (schema v%d)", config.data_dir, services.db.version())
    log.info("recordings: %s (%d HTTPS replies, %d NEX methods, %d offers)", cfg.recordings_dir,
             len(services.assets.http.entries), len(services.assets.rmc.data), len(services.assets.catalog))
    if services.assets.empty:
        log.warning("no recordings found - the game will get empty replies for everything not implemented")

    plugins = PluginManager(services)
    plugins.load_dir(plugins_dir(config))

    # The NEX handshake is signed with this key; its public half goes into the rendezvous config.
    signing_key = secure.load_or_create_signing_key(config.data_dir / "nex_signing_key.pem")
    public_key = secure.config_public_key(signing_key)

    app = https_app.build_app(services, plugins, nex_public_key=public_key)
    pki = tls.ensure_pki(config.data_dir / "tls")
    runner = await https_app.start(app, cfg.bind, cfg.https_port, pki.server_pem)
    log.info("HTTPS listening on %s:%d", cfg.bind, cfg.https_port)
    crl_runner = None
    try:
        crl_runner = await https_app.start_crl(pki.crl, cfg.crl_port)
        log.info("CRL on http://127.0.0.1:%d%s (local CA %s)", cfg.crl_port, tls.CRL_PATH, pki.ca_thumbprint)
    except OSError as e:
        log.warning("cannot serve the CRL on 127.0.0.1:%d (%s): Ubisoft Connect will reject our "
                    "certificate", cfg.crl_port, e)

    dispatcher = RmcDispatcher(services, plugins)
    nex, transport = await nex_server.start(services, dispatcher, signing_key, cfg.bind, cfg.public_host,
                                            cfg.nex_port)
    log.info("NEX listening on udp %s:%d (game reaches it at %s)", cfg.bind, cfg.nex_port, cfg.public_host)
    try:
        await nex.run_timers()
    finally:
        transport.close()
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
