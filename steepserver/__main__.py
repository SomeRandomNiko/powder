"""steepserver run | steepserver admin ...   (run from the server/ folder: uv run steepserver run)"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .admin import cli as admin_cli
from .config import Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steepserver", description="Offline server for Steep")
    parser.add_argument("-c", "--config", type=Path, default=Path("config.toml"),
                        help="config file (default: ./config.toml; built-in defaults if missing)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the server")
    admin_cli.add_parser(sub)
    args = parser.parse_args(argv)
    config = Config.load(args.config)

    if args.cmd == "run":
        from . import app
        app.main(config)
        return 0
    return admin_cli.run(args, config)


if __name__ == "__main__":
    sys.exit(main())
