"""The local CA in Windows' trusted roots (current user), which Ubisoft Connect needs.

Used by `steepserver admin trust` and by the launcher. Only the CA certificate is installed (never a key);
it is name-constrained to the Ubisoft domains, see tls.py. The current user's store needs no admin rights;
Windows itself asks the user to confirm an install.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes


class TrustError(RuntimeError):
    pass


def thumbprint(ca_pem: Path) -> str:
    """SHA-1 thumbprint as Windows shows it."""
    return x509.load_pem_x509_certificate(ca_pem.read_bytes()).fingerprint(hashes.SHA1()).hex().upper()


def _certutil(*args: str) -> subprocess.CompletedProcess:
    if sys.platform != "win32":
        raise TrustError("only needed on Windows (Ubisoft Connect)")
    return subprocess.run(["certutil", "-user", *args], capture_output=True, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def trusted_thumbprints() -> set[str]:
    out = _certutil("-store", "Root").stdout
    return {m.replace(" ", "").upper() for m in re.findall(r"Cert Hash\(sha1\): ([0-9a-fA-F ]+)", out)}


def is_trusted(ca_pem: Path) -> bool:
    return thumbprint(ca_pem) in trusted_thumbprints()


def install(ca_pem: Path) -> None:
    """Add the CA to the current user's trusted roots. Windows shows a confirmation dialog; declining it
    makes certutil fail, which raises TrustError."""
    r = _certutil("-addstore", "Root", str(ca_pem))
    if r.returncode or not is_trusted(ca_pem):
        raise TrustError(f"certificate not installed (certutil exit {r.returncode})")


def remove(ca_pem: Path) -> None:
    thumb = thumbprint(ca_pem)
    if thumb in trusted_thumbprints():
        r = _certutil("-delstore", "Root", thumb)
        if r.returncode:
            raise TrustError(f"certutil -delstore failed ({r.returncode})")


def other_tool_roots() -> list[str]:
    """Trusted roots from capture tooling that should not linger (e.g. mitmproxy)."""
    out = _certutil("-store", "Root").stdout
    return ["mitmproxy"] if "CN=mitmproxy" in out else []
