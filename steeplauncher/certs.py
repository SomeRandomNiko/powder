"""The server's CA must be trusted by Windows for Ubisoft Connect. We fetch the CA certificate from the
server (/_steep/ca.pem; works the same for a local or remote server), keep a copy, and install it into
the current user's trusted roots when asked (Windows shows its own confirmation)."""
from __future__ import annotations

from pathlib import Path

from steepserver import trust

EXPLANATION = (
    "Ubisoft Connect only talks to servers whose certificate Windows trusts. The server has its own small "
    "certificate authority that is technically limited to Ubisoft's domains, so it cannot vouch for any "
    "other website. It is added for your Windows user only (no admin rights). When you click \"Install "
    "certificate\", Windows shows a security warning asking you to confirm it; answer Yes. Restore system "
    "removes it again.")


def save_ca(pem: bytes, data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "server-ca.pem"
    path.write_bytes(pem)
    return path


def is_trusted(ca_path: Path) -> bool:
    return trust.is_trusted(ca_path)


def install(ca_path: Path) -> None:
    trust.install(ca_path)
