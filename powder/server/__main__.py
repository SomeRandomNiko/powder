"""powder-server run | powder-server admin ...   (in a checkout: uv run powder-server run; installed:
powder-cli.exe server run)"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import paths
from ..version import __version__

from .admin import cli as admin_cli
from .config import Config
from .gamedata import cli as gamedata_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="powder-server", description="Powder: stand-in for Steep's online services")
    parser.add_argument("-c", "--config", type=Path, default=paths.user_root() / "config.toml",
                        help="config file (default: config.toml in Powder's folder; built-in defaults if missing)")
    parser.add_argument("--version", action="version", version=f"Powder {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the server")
    admin_cli.add_parser(sub)
    gamedata_cli.add_parser(sub)
    args = parser.parse_args(argv)
    config = Config.load(args.config)

    if args.cmd == "run":
        from . import app
        app.main(config)
        return 0
    if args.cmd == "gamedata":
        return gamedata_cli.run(args, config)
    return admin_cli.run(args, config)


if __name__ == "__main__":
    sys.exit(main())
