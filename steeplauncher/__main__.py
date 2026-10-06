"""uv run steep-launcher [-c launcher.toml] [--status | --restore]"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import LauncherConfig
from .session import Session, restore_steps


class ConsoleSession(Session):
    """The same flows without a window: steps are printed, choices are asked on the console."""

    def ask(self, key, text, actions, details=""):
        print(f"\n{self.step(key).title}: {text}")
        for i, a in enumerate(actions, 1):
            print(f"  {i}) {a}")
        while True:
            pick = input("> ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(actions):
                return actions[int(pick) - 1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="steep-launcher")
    p.add_argument("-c", "--config", type=Path, default=Path("launcher.toml"))
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="show what the launcher has set up on this PC")
    mode.add_argument("--restore", action="store_true", help="undo it (hosts entries, certificate) in the console")
    args = p.parse_args(argv)
    cfg = LauncherConfig.load(args.config)
    data_dir = (cfg.path.resolve().parent if cfg.path else Path.cwd()) / "data" / "launcher"
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(data_dir / "logs" / "launcher.log", encoding="utf-8"),
                                       logging.StreamHandler(sys.stdout)]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
                        handlers=handlers if not args.status else handlers[:1])
    if args.status:
        from .footprint import scan
        fp = scan(cfg)
        print("\n".join(fp.lines()))
        print("nothing of the launcher's is set up" if fp.clean else
              "use --restore (or Restore system... in the launcher) to undo it")
        return 0
    if args.restore:
        s = ConsoleSession(cfg, data_dir)
        s.steps = restore_steps()
        s._run(s.restore_flow())
        return 0 if all(st.state == "ok" for st in s.steps) else 1
    from .ui import LauncherWindow
    LauncherWindow(Session(cfg, data_dir)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
