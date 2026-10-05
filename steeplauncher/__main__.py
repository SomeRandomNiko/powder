"""uv run steep-launcher [-c launcher.toml]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import LauncherConfig
from .session import Session


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="steep-launcher")
    p.add_argument("-c", "--config", type=Path, default=Path("launcher.toml"))
    args = p.parse_args(argv)
    cfg = LauncherConfig.load(args.config)
    data_dir = (cfg.path.resolve().parent if cfg.path else Path.cwd()) / "data" / "launcher"
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
                        handlers=[logging.FileHandler(data_dir / "logs" / "launcher.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    from .ui import LauncherWindow
    LauncherWindow(Session(cfg, data_dir)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
