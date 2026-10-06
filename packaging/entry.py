"""Entry point of the packaged program (Powder.exe / powder-cli.exe)."""
import multiprocessing
import sys

from powder.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
