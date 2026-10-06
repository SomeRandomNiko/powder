"""One program for everything (what the installed Powder.exe / powder-cli.exe run):

  powder [--status | --restore [--window]]     the launcher (default)
  powder server run | admin ... | gamedata ... the server and its commands (powder-server in a checkout)
  powder hostsfix add|remove ...               the elevated hosts-file step (started by the launcher)
  powder --version
"""
from __future__ import annotations

import sys

from .version import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["server"]:
        from .server.__main__ import main as server_main
        return server_main(argv[1:])
    if argv[:1] == ["hostsfix"]:
        from .launcher.hostsfix import main as hostsfix_main
        return hostsfix_main(argv[1:])
    if argv[:1] in (["--version"], ["version"]):
        print(f"Powder {__version__}")
        return 0
    from .launcher.__main__ import main as launcher_main
    return launcher_main(argv)


if __name__ == "__main__":
    sys.exit(main())
