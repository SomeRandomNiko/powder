"""
Steep NEX / Quazal Rendez-Vous PRUDP (classic "V0") transport codec.

Derived from observing the game's own traffic (the analysis notes are kept outside this
repository). The packet layout is covered by tests/test_codecs.py with self-made packets.

Packet layout (little-endian):
    off size field
     0   1   source stream byte   (0x3f = client/game side, 0x31 = server side)
     1   1   dest   stream byte
     2   1   type_flags           type = b & 0x07 ; flags = b >> 3
     3   1   session_id
     4   4   signature            = the peer's connection_signature (0 in SYN)
     8   2   sequence_id          u16 LE
    --- by type ---
    SYN / CONNECT : +4  connection_signature  (this side's own; 0 in the client SYN)
    DATA          : +1  fragment_id
    --- then ---
         N   payload              (encrypted for CONNECT/DATA; empty for SYN & pure ACK)
         4   checksum             32-bit, see checksum()

CONNECT key exchange (type_flags bit 0x80, mirrors the game's parser FUN_7ff661b08f80):
    if FLAG_ACK:  [u16 len][ECDSA signature]     (server CONNECT-ACK only)
                  [u16 len][ECK1 ECDH public blob]
Then, for any type, if FLAG_HAS_SIZE (0x40): [u16 payload size]; otherwise the payload runs
to the 4-byte checksum. Payloads of stream types 2/3 are AES-CBC encrypted — see secure.py.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# PRUDP packet types (low 3 bits of type_flags)
SYN, CONNECT, DATA, DISCONNECT, PING = 0, 1, 2, 3, 4

# PRUDP flag bits (as they sit in the type_flags byte, i.e. already shifted << 3)
FLAG_ACK = 0x08
FLAG_RELIABLE = 0x10
FLAG_NEED_ACK = 0x20
FLAG_HAS_SIZE = 0x40      # u16 payload size present (unused in the capture)
FLAG_KEY_EXCHANGE = 0x80  # CONNECT carries [sig] + ECK1 ECDH blob

# Access-key-derived checksum base. Connection-independent (identical both directions in
# the capture) -> purely a function of Steep's Quazal access key. Recovered empirically;
# lets us produce valid checksums WITHOUT knowing the access-key string. See notes.
CHECKSUM_BASE = 0xB7E689A7

CLIENT_STREAM = 0x3F
SERVER_STREAM = 0x31


def checksum(data: bytes) -> bytes:
    """4-byte PRUDP checksum over the whole packet up to (not incl.) the checksum field.

    checksum32 = (CHECKSUM_BASE + sum(u32 LE words of data, final word zero-padded)) & 0xFFFFFFFF
    """
    total = CHECKSUM_BASE
    for i in range(0, len(data), 4):
        word = data[i:i + 4].ljust(4, b"\x00")
        total = (total + struct.unpack("<I", word)[0]) & 0xFFFFFFFF
    return struct.pack("<I", total)


@dataclass
class Packet:
    source: int = CLIENT_STREAM
    dest: int = SERVER_STREAM
    type: int = DATA
    flags: int = 0                      # flag bits as they sit in the type_flags byte
    session_id: int = 0
    signature: bytes = b"\x00\x00\x00\x00"
    sequence_id: int = 0
    connection_signature: bytes = b"\x00\x00\x00\x00"  # SYN/CONNECT only
    fragment_id: int = 0                # DATA only
    key_signature: bytes = b""          # CONNECT+KEY_EXCHANGE+ACK only (ECDSA r||s)
    key_blob: bytes = b""               # CONNECT+KEY_EXCHANGE only (BCrypt ECK1 blob)
    payload: bytes = b""

    @property
    def type_flags(self) -> int:
        return (self.type & 0x07) | (self.flags & ~0x07)

    def encode(self) -> bytes:
        hdr = struct.pack(
            "<BBBB4sH",
            self.source, self.dest, self.type_flags, self.session_id,
            self.signature, self.sequence_id,
        )
        body = b""
        if self.type in (SYN, CONNECT):
            body += self.connection_signature[:4].ljust(4, b"\x00")
        if self.type == CONNECT and self.flags & FLAG_KEY_EXCHANGE:
            if self.flags & FLAG_ACK:
                body += struct.pack("<H", len(self.key_signature)) + self.key_signature
            body += struct.pack("<H", len(self.key_blob)) + self.key_blob
        if self.type == DATA:
            body += bytes([self.fragment_id & 0xFF])
        if self.flags & FLAG_HAS_SIZE:
            body += struct.pack("<H", len(self.payload))
        body += self.payload
        pkt = hdr + body
        return pkt + checksum(pkt)

    @classmethod
    def decode(cls, raw: bytes) -> "Packet":
        if len(raw) < 14:
            raise ValueError(f"PRUDP packet too short: {len(raw)} bytes")
        body, got_cksum = raw[:-4], raw[-4:]
        want = checksum(body)
        if got_cksum != want:
            raise ValueError(f"bad checksum: got {got_cksum.hex()} want {want.hex()}")
        source, dest, type_flags, session_id, signature, sequence_id = struct.unpack_from("<BBBB4sH", body, 0)
        p = cls(
            source=source, dest=dest,
            type=type_flags & 0x07, flags=type_flags & ~0x07,
            session_id=session_id, signature=signature, sequence_id=sequence_id,
        )
        off = 10
        if p.type in (SYN, CONNECT):
            p.connection_signature = body[off:off + 4]
            off += 4

        def field() -> bytes:
            nonlocal off
            (n,) = struct.unpack_from("<H", body, off)
            off += 2 + n
            return body[off - n:off]

        if p.type == CONNECT and p.flags & FLAG_KEY_EXCHANGE:
            if p.flags & FLAG_ACK:
                p.key_signature = field()
            p.key_blob = field()
        if p.type == DATA:
            p.fragment_id = body[off]
            off += 1
        if p.flags & FLAG_HAS_SIZE:
            p.payload = field()
        else:
            p.payload = body[off:]
        return p

    def ack(self) -> "Packet":
        """Build the ACK for this (NEED_ACK) packet, swapping direction."""
        return Packet(
            source=self.dest, dest=self.source,
            type=self.type, flags=FLAG_ACK,
            session_id=self.session_id,
            signature=self.signature,
            sequence_id=self.sequence_id,
            fragment_id=self.fragment_id if self.type == DATA else 0,
        )
