"""Logging: console + data/logs/server.log. Modules use logging.getLogger("steep.<area>")."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)-12s %(message)s"


def setup(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("steep")
    root.setLevel(level.upper())
    root.handlers.clear()
    fmt = logging.Formatter(FORMAT, datefmt="%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = logging.handlers.RotatingFileHandler(log_dir / "server.log", maxBytes=20_000_000,
                                                backupCount=5, encoding="utf-8")
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)
    root.propagate = False
