"""Local CA + server certificate + CRL (what Ubisoft Connect checks through Windows)."""
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

from steepserver import tls


def _server_cert(pki):
    return x509.load_pem_x509_certificates(pki.server_pem.read_bytes())[0]


def test_chain_and_constraints(tmp_path):
    pki = tls.ensure_pki(tmp_path)
    ca, leaf = pki.ca_cert, _server_cert(pki)
    # leaf is signed by the CA
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256()))
    assert leaf.issuer == ca.subject
    # CA may only vouch for the Ubisoft domains and loopback
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical
    assert set(nc.value.permitted_subtrees) == {x509.DNSName("ubi.com"), x509.DNSName("ubisoft.com"),
                                                x509.IPAddress(ipaddress.ip_network("127.0.0.0/8"))}
    bc = ca.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca and bc.path_length == 0
    # every leaf name lies inside the constraints
    for name in leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value \
            .get_values_for_type(x509.DNSName):
        assert name.rstrip(".").endswith(("ubi.com", "ubisoft.com"))
    assert "dmx.upc.ubisoft.com" in tls.HOSTNAMES
    cdp = leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    assert cdp[0].full_name[0].value == tls.CRL_URL


def test_crl_is_signed_by_ca(tmp_path):
    pki = tls.ensure_pki(tmp_path)
    crl = x509.load_der_x509_crl(pki.crl())
    assert crl.is_signature_valid(pki.ca_cert.public_key())
    assert len(crl) == 0
    assert crl.next_update_utc > crl.last_update_utc


def test_reuse_and_replace_old_self_signed(tmp_path):
    pki = tls.ensure_pki(tmp_path)
    first = pki.server_pem.read_bytes()
    assert tls.ensure_pki(tmp_path).server_pem.read_bytes() == first          # reused
    # an old-style self-signed server.pem is replaced; the CA stays
    pki.server_pem.write_bytes(b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    again = tls.ensure_pki(tmp_path)
    assert again.ca_thumbprint == pki.ca_thumbprint
    assert _server_cert(again).issuer == pki.ca_cert.subject
