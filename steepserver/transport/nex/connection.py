"""One PRUDP connection (one game client): reliable, ordered, fragmented, encrypted message stream.

Reliability rules (from the prototype, which the game accepted live):
  * every client packet with NEED_ACK is acked (DATA acks echo the fragment id)
  * client DATA is decrypted, buffered by sequence id and delivered in order; fragments are joined
    until fragment_id == 0 (the last fragment)
  * our DATA is sent RELIABLE|NEED_ACK in FRAGMENT_SIZE chunks and retransmitted until acked
Fixed vs. the prototype: 16-bit sequence ids compare wrap-safe; a connection that stops answering
(retransmits exhausted) or goes silent (IDLE_TIMEOUT_S) is closed instead of lingering forever.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from . import rmc_codec, secure
from .prudp import DATA, FLAG_ACK, FLAG_NEED_ACK, FLAG_RELIABLE, Packet

log = logging.getLogger("steep.nex")

FRAGMENT_SIZE = 700          # plaintext bytes per DATA fragment (the game sends ~730-780 B)
RETRANSMIT_S = 0.5
MAX_TRIES = 12
IDLE_TIMEOUT_S = 60.0        # the game PINGs every few seconds while connected


def seq_before(a: int, b: int) -> bool:
    """True if 16-bit sequence id `a` comes before `b` (wrap-safe)."""
    return ((a - b) & 0xFFFF) > 0x8000


@dataclass(eq=False)
class PrudpConnection:
    addr: tuple[str, int]
    sendto: Callable[[bytes, tuple], None]
    pid: int = 0                                  # our id for this connection (StationURL PID, NEX cid)
    client_stream: int = 0x3F
    server_stream: int = 0x31
    server_sig: bytes = field(default_factory=lambda: os.urandom(4))
    session_id: int = field(default_factory=lambda: os.urandom(1)[0] or 1)
    client_sig: bytes = b"\x00" * 4
    key: bytes | None = None
    connect_ack: bytes | None = None              # cached, resent if the client repeats CONNECT
    expected_seq: int = 0                         # next client DATA sequence id to deliver
    pending: dict = field(default_factory=dict)   # client seq -> (fragment_id, plaintext)
    fragments: list = field(default_factory=list)
    next_seq: int = 1                             # our next reliable DATA sequence id
    unacked: dict = field(default_factory=dict)   # seq -> [raw, last_sent, tries]
    last_seen: float = field(default_factory=time.monotonic)
    closed: bool = False
    session: object = None                        # core.sessions.PlayerSession after LoginWithToken
    _next_call_id: int = 0x10000                  # ids for server-initiated requests

    # ---- packets --------------------------------------------------------------------------------
    def reply(self, **kw) -> Packet:
        return Packet(source=self.server_stream, dest=self.client_stream,
                      session_id=kw.pop("session_id", self.session_id),
                      signature=kw.pop("signature", self.client_sig), **kw)

    def send(self, p: Packet, reliable: bool = False) -> None:
        raw = p.encode()
        self.sendto(raw, self.addr)
        if reliable:
            self.unacked[p.sequence_id] = [raw, time.monotonic(), 1]

    def on_ack(self, p: Packet) -> None:
        self.unacked.pop(p.sequence_id, None)

    def receive_data(self, p: Packet) -> list[bytes]:
        """Handle a client DATA packet; returns the complete messages it finishes (in order)."""
        if p.flags & FLAG_NEED_ACK:
            self.send(self.reply(type=DATA, flags=FLAG_ACK, sequence_id=p.sequence_id,
                                 fragment_id=p.fragment_id))
        if seq_before(p.sequence_id, self.expected_seq) or p.sequence_id in self.pending:
            return []                                                     # duplicate
        try:
            plain = secure.decrypt_payload(self.key, p.payload, p.sequence_id) if self.key and p.payload \
                else p.payload
        except Exception as e:  # noqa: BLE001
            log.warning("%s: undecryptable DATA seq %d (%s)", self.addr, p.sequence_id, e)
            return []
        self.pending[p.sequence_id] = (p.fragment_id, plain)
        messages = []
        while self.expected_seq in self.pending:
            frag_id, chunk = self.pending.pop(self.expected_seq)
            self.expected_seq = (self.expected_seq + 1) & 0xFFFF
            self.fragments.append(chunk)
            if frag_id == 0:
                messages.append(b"".join(self.fragments))
                self.fragments = []
        return messages

    # ---- messages -------------------------------------------------------------------------------
    def send_message(self, data: bytes) -> None:
        if self.closed:
            return
        chunks = [data[i:i + FRAGMENT_SIZE] for i in range(0, len(data), FRAGMENT_SIZE)] or [b""]
        for i, chunk in enumerate(chunks):
            seq = self.next_seq
            self.next_seq = (self.next_seq + 1) & 0xFFFF
            frag_id = 0 if i == len(chunks) - 1 else i + 1
            payload = secure.encrypt_payload(self.key, seq, chunk) if self.key else chunk
            self.send(self.reply(type=DATA, flags=FLAG_RELIABLE | FLAG_NEED_ACK, sequence_id=seq,
                                 fragment_id=frag_id, payload=payload), reliable=True)

    def send_request(self, protocol: str, method: str, params: bytes, call_id: int | None = None) -> int:
        """Server-initiated RMC request (e.g. NotificationProtocol::ProcessNotificationEvent_V1,
        which the real server sends with call id 0). Returns the call id used."""
        if call_id is None:
            call_id = self._next_call_id
            self._next_call_id += 1
        self.send_message(rmc_codec.build_request(protocol, call_id, method, params))
        return call_id

    # ---- timers ---------------------------------------------------------------------------------
    def tick(self, now: float) -> bool:
        """Retransmit overdue packets. Returns False when the connection should be dropped."""
        if self.closed:
            return False
        if now - self.last_seen > IDLE_TIMEOUT_S:
            log.info("%s: idle for %.0fs, dropping connection", self.addr, now - self.last_seen)
            return False
        for seq, entry in list(self.unacked.items()):
            raw, last, tries = entry
            if now - last < RETRANSMIT_S:
                continue
            if tries >= MAX_TRIES:
                log.warning("%s: seq %d never acked after %d tries, dropping connection", self.addr, seq, tries)
                return False
            self.sendto(raw, self.addr)
            entry[1], entry[2] = now, tries + 1
        return True
