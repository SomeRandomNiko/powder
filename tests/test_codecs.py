"""PRUDP, secure channel and RMC codecs (self-made packets and keys)."""
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from powder.server.transport.nex import rmc_codec as rmc
from powder.server.transport.nex import secure
from powder.server.transport.nex.prudp import (CLIENT_STREAM, CONNECT, DATA, FLAG_ACK, FLAG_HAS_SIZE,
                                             FLAG_KEY_EXCHANGE, FLAG_NEED_ACK, FLAG_RELIABLE, SERVER_STREAM,
                                             SYN, Packet, checksum)

CLIENT_SIG = bytes.fromhex("11223344")     # made-up connection signatures
SERVER_SIG = bytes.fromhex("a1b2c3d4")


def _connect_pair():
    """A CONNECT / CONNECT-ACK key exchange built with freshly generated keys."""
    signing = ec.generate_private_key(ec.SECP256R1())
    game = ec.generate_private_key(ec.SECP256R1())
    client_blob = secure.to_blob(game.public_key(), secure.ECK1)
    server_blob, sig, _ = secure.server_handshake(signing, client_blob)
    connect = Packet(type=CONNECT, flags=FLAG_KEY_EXCHANGE | FLAG_RELIABLE | FLAG_NEED_ACK, signature=SERVER_SIG,
                     sequence_id=1, connection_signature=CLIENT_SIG, key_blob=client_blob)
    ack = Packet(source=SERVER_STREAM, dest=CLIENT_STREAM, type=CONNECT, flags=FLAG_KEY_EXCHANGE | FLAG_ACK,
                 signature=CLIENT_SIG, sequence_id=1, connection_signature=SERVER_SIG, key_signature=sig,
                 key_blob=server_blob, payload=bytes(32))
    return signing, connect, ack


# self-made packets of every kind the handshake uses (layout as the game sends them)
VECTORS = [
    (Packet(type=SYN, flags=FLAG_NEED_ACK), "SYN game->srv"),
    (Packet(source=SERVER_STREAM, dest=CLIENT_STREAM, type=SYN, flags=FLAG_ACK, connection_signature=SERVER_SIG),
     "SYN-ACK srv->game"),
    (Packet(source=SERVER_STREAM, dest=CLIENT_STREAM, type=DATA, flags=FLAG_ACK, session_id=0x11,
            signature=CLIENT_SIG, sequence_id=2, fragment_id=1), "ACK srv->game (seq2 frag1)"),
    (Packet(type=DATA, flags=FLAG_ACK, signature=SERVER_SIG, sequence_id=1), "ACK game->srv (seq1)"),
    (Packet(type=DATA, flags=FLAG_RELIABLE | FLAG_NEED_ACK | FLAG_HAS_SIZE, signature=SERVER_SIG,
            sequence_id=3, payload=b""), "DATA with size field"),
]


def test_prudp_header_layout():
    raw = VECTORS[0][0].encode()
    assert raw[:4] == bytes([CLIENT_STREAM, SERVER_STREAM, SYN | FLAG_NEED_ACK, 0])
    assert len(raw) == 18 and checksum(raw[:-4]) == raw[-4:]


@pytest.mark.parametrize("pkt,desc", VECTORS, ids=[d for _, d in VECTORS])
def test_prudp_vectors_roundtrip(pkt, desc):
    raw = pkt.encode()
    assert checksum(raw[:-4]) == raw[-4:]
    assert Packet.decode(raw) == pkt and Packet.decode(raw).encode() == raw


def test_connect_key_exchange_roundtrip():
    signing, connect, ack = _connect_pair()
    for p in (connect, ack):
        d = Packet.decode(p.encode())
        assert d.key_blob[:4] == b"ECK1" and len(d.key_blob) == 72
        assert len(d.key_signature) == (64 if p.flags & FLAG_ACK else 0)
        assert len(d.payload) == (32 if p.flags & FLAG_ACK else 0)
    d = Packet.decode(ack.encode())
    cfg_pub = secure.from_blob(base64.b64decode(secure.config_public_key(signing)))
    assert secure.verify_blob(cfg_pub, d.key_blob, d.key_signature)


def test_handshake_and_payloads():
    signing = ec.generate_private_key(ec.SECP256R1())
    cfg_pub = secure.from_blob(base64.b64decode(secure.config_public_key(signing)))
    game = ec.generate_private_key(ec.SECP256R1())
    blob, sig, key = secure.server_handshake(signing, secure.to_blob(game.public_key(), secure.ECK1))
    assert secure.verify_blob(cfg_pub, blob, sig)
    assert secure.session_key(game, blob) == key and len(key) == 16
    tail = secure.encrypt_payload(key, 1, b"")
    assert len(tail) == 32 and secure.decrypt_payload(key, tail, 1) == b""
    msg = b"\x2a\x00\x00\x00" + b"RMC" * 40
    for compress in (False, True):
        assert secure.decrypt_payload(key, secure.encrypt_payload(key, 7, msg, compress=compress), 7) == msg
    with pytest.raises(ValueError):
        secure.decrypt_payload(key, secure.encrypt_payload(key, 7, msg), 8)


def test_rmc_request_response_error():
    req = rmc.parse(rmc.build_request("LoginProtocol", 7, "LoginProtocol::Register_V1", b"\x01\x02"))
    assert req.is_request and req.call_id == 7 and req.body == b"\x01\x02"
    resp = rmc.parse(rmc.build_response("LoginProtocol", 7, "LoginProtocol::Register_V1", b"\x09"))
    assert not resp.is_request and resp.success and resp.method == "LoginProtocol::Register_V1*"
    err = rmc.parse(rmc.build_error("Foo", 9, "Core", 2))
    assert not err.success and err.error == ("Core", 2) and err.call_id == 9


def test_quazal_datetime_packing():
    """Regression value: Quazal date-time packing of 2026-10-04 17:18:32 UTC."""
    import datetime
    assert rmc.quazal_datetime(datetime.datetime(2026, 10, 4, 17, 18, 32)) == 136005096608
