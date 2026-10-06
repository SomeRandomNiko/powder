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


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def supported_build(path: Path) -> str | None:
    """Name of the supported build this exe is, or None if unknown."""
    return SUPPORTED.get(file_sha256(path))
