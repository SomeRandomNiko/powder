"""The launcher's TLS helper only fits one build of Steep.exe (the code offset it relies on is specific
to that build). We refuse to attach to anything else, with a clear message, rather than risk a crash."""
from __future__ import annotations

import hashlib
from pathlib import Path

# SHA-256 of the supported Steep.exe builds. Add more as they are verified.
# The Ubisoft Connect download is the same compiled game as the Steam one (same link time and section
# layout); only the protector's packaging differs. Verified in memory: the patched function is
# byte-identical and every other code difference is a call into the protector or a load-address value.
SUPPORTED = {
    "37ebfab4284892f448b692cfa8a812479d1f35de6b66d1478da8f6f955d819e9": "Steep (Steam, TU7)",
    "e8b14fe6f3b45975080f1b8e78249bf8fefbbd73ee4c182156c0b8a66452e8cb": "Steep (Ubisoft Connect, TU7)",
}


# Where the TLS helper sets its trap (ssl_verify_cert_chain; also in native/*.c), and what the first
# PATCH_SPOT_LEN bytes there hash to once the protector has unpacked the game code (the same in both
# builds). The launcher attaches the helper only after that: during unpacking the protector still writes
# into that page, and the helper's read-only trap would crash it (seen with Steep on a hard disk).
TARGET_RVA = 0x1D3E290
PATCH_SPOT_LEN = 64
PATCH_SPOT_SHA256 = "710263eff728e572e93557ae30b9b125415ed61657273c967ae8822b66c0e8e9"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def supported_build(path: Path) -> str | None:
    """Name of the supported build this exe is, or None if unknown."""
    return SUPPORTED.get(file_sha256(path))
