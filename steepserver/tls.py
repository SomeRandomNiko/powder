"""TLS certificate for the HTTPS server, generated on first run (never committed).

The game is made to accept any certificate (in-process hook), so a self-signed one for the
UbiServices / Olympus hostnames is enough. Stored as key + certificate PEM in the data directory.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger("steep.tls")

HOSTNAMES = [
    "public-ubiservices.ubi.com",
    "msr-public-ubiservices.ubi.com",
    "public-im-ubiservices.ubi.com",
    "public-ws-ubiservices.ubi.com",
    "steep-prod-pc.steep.olympus.ubi.com",
    "*.ubiservices.ubi.com",
    "*.olympus.ubi.com",
    "*.ubi.com",
]


def ensure_certificate(path: Path) -> Path:
    """Return a PEM file with key + certificate, creating it if missing."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAMES[0]),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "steepserver (local)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(h) for h in HOSTNAMES] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption())
                     + cert.public_bytes(serialization.Encoding.PEM))
    log.info("generated TLS certificate %s", path)
    return path
