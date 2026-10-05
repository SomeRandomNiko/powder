"""TLS for the HTTPS and demux servers: a local CA, a server certificate, and the CA's CRL.

The game accepts any certificate (in-process hook), but Ubisoft Connect checks certificates through
Windows, so it needs a chain to a root it trusts plus revocation information:

- **local CA** (`ca.pem` + `ca-key.pem`): restricted by NameConstraints to the Ubisoft domains and
  loopback, so trusting it (`steepserver admin trust install`) cannot let it vouch for any other site,
  even if its key leaked.
- **server certificate** (`server.pem` = key + certificate + CA): all Ubisoft hostnames we answer,
  with a CRL distribution point on this machine.
- **CRL**: empty, signed by the CA, served over plain HTTP (`CRL_URL`); Windows refuses a certificate
  whose revocation status it cannot check.

Everything is generated on first run into `data/tls/` and never committed.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .hostnames import CERT_NAMES, CRL_HOST

log = logging.getLogger("steep.tls")

HOSTNAMES = CERT_NAMES
PERMITTED_DOMAINS = ["ubi.com", "ubisoft.com"]          # NameConstraints: these and their subdomains
PERMITTED_NET = ipaddress.ip_network("127.0.0.0/8")
CRL_PATH = "/steep-ca.crl"
CRL_URL = f"http://{CRL_HOST}{CRL_PATH}"                 # plain HTTP on port 80 of the server: a redirected
                                                         # hostname, so it works wherever the server runs
CA_NAME = "steepserver local CA"
SERVER_ORG = "steepserver (local)"

_DAY = datetime.timedelta(days=1)
CA_LIFETIME = 3650 * _DAY
SERVER_LIFETIME = 825 * _DAY
CRL_LIFETIME = 7 * _DAY


@dataclass
class Pki:
    ca_cert: x509.Certificate
    ca_key: ec.EllipticCurvePrivateKey
    server_pem: Path                                     # key + server certificate + CA (for ssl)
    ca_pem: Path                                         # what `admin trust install` adds to Windows

    def crl(self) -> bytes:
        """An empty CRL, freshly signed (DER). Cheap enough to make per request."""
        now = _now()
        crl = (x509.CertificateRevocationListBuilder()
               .issuer_name(self.ca_cert.subject)
               .last_update(now - _DAY)
               .next_update(now + CRL_LIFETIME)
               .add_extension(x509.CRLNumber(int(now.timestamp())), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()),
                              critical=False)
               .sign(self.ca_key, hashes.SHA256()))
        return crl.public_bytes(serialization.Encoding.DER)

    @property
    def ca_thumbprint(self) -> str:
        """SHA-1 thumbprint as Windows shows it (certutil, Cert:\\ drive)."""
        return self.ca_cert.fingerprint(hashes.SHA1()).hex().upper()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _make_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, SERVER_ORG)])
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _DAY).not_valid_after(now + CA_LIFETIME)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(d) for d in PERMITTED_DOMAINS]
                + [x509.IPAddress(PERMITTED_NET)],
                excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return cert, key


def _make_server(ca_cert: x509.Certificate, ca_key) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now()
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAMES[0]),
                                     x509.NameAttribute(NameOID.ORGANIZATION_NAME, SERVER_ORG)]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _DAY).not_valid_after(now + SERVER_LIFETIME)
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(h) for h in HOSTNAMES] + [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(
                full_name=[x509.UniformResourceIdentifier(CRL_URL)], relative_name=None,
                reasons=None, crl_issuer=None)]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    return cert, key


def _load_ca(ca_pem: Path, ca_key_pem: Path):
    if not (ca_pem.exists() and ca_key_pem.exists()):
        return None
    return (x509.load_pem_x509_certificate(ca_pem.read_bytes()),
            serialization.load_pem_private_key(ca_key_pem.read_bytes(), password=None))


def _server_ok(server_pem: Path, ca_cert: x509.Certificate) -> bool:
    """An existing server.pem is reused only if this CA issued it, it is not about to expire, and it has
    the current names and CRL point. (Earlier versions' certificates are replaced automatically.)"""
    if not server_pem.exists():
        return False
    try:
        cert = x509.load_pem_x509_certificates(server_pem.read_bytes())[0]
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        cdp = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    except (ValueError, x509.ExtensionNotFound):
        return False
    return (cert.issuer == ca_cert.subject and cert.subject != cert.issuer
            and cert.not_valid_after_utc - _now() > 30 * _DAY
            and set(HOSTNAMES) <= set(names.get_values_for_type(x509.DNSName))
            and [p.full_name[0].value for p in cdp] == [CRL_URL])


def ensure_pki(tls_dir: Path) -> Pki:
    """Load the CA and server certificate from `tls_dir`, creating whatever is missing or outdated."""
    tls_dir.mkdir(parents=True, exist_ok=True)
    ca_pem, ca_key_pem, server_pem = tls_dir / "ca.pem", tls_dir / "ca-key.pem", tls_dir / "server.pem"
    loaded = _load_ca(ca_pem, ca_key_pem)
    if loaded is None:
        ca_cert, ca_key = _make_ca()
        ca_key_pem.write_bytes(_key_pem(ca_key))
        ca_pem.write_bytes(_cert_pem(ca_cert))
        log.info("generated local CA %s (install it for Ubisoft Connect: steepserver admin trust install)",
                 ca_pem)
    else:
        ca_cert, ca_key = loaded
    if not _server_ok(server_pem, ca_cert):
        cert, key = _make_server(ca_cert, ca_key)
        server_pem.write_bytes(_key_pem(key) + _cert_pem(cert) + _cert_pem(ca_cert))
        log.info("generated server certificate %s", server_pem)
    return Pki(ca_cert, ca_key, server_pem, ca_pem)
