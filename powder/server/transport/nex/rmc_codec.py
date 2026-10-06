"""
NEX RMC message codec (Ubisoft's name-based variant). Reconstructed from the binary; notes/nex-recon.md.

  request : [u32 size][String protocol][u8 1][u32 callID][String "Proto::Method_V1"][params...]
  response: [u32 size][String protocol][u8 0][u8 1][u32 callID][String "Proto::Method_V1*"][outputs...]
  error   : [u32 size][String protocol][u8 0][u8 0][String errName][u16 code][u32 callID]

String = [u16 len incl. NUL][bytes][NUL]. GUID = 16 bytes in Windows (bytes_le) order.
Many requests start their params with a type-version map: [u32 n] n x [String typeName][u16 version].
`size` excludes itself (classic Quazal); parse() tolerates either.

A response's method name is the request's name plus "*": the client dispatcher (FUN_7ff661b5bd20)
_stricmp's against name + "*". Without it the reply counts as an unknown method, the call fails and
the game shows DE-005-03 (found by live trace, 2026-10-04).
"""
from __future__ import annotations

import datetime
import struct
import uuid
from dataclasses import dataclass

RESPONSE_SUFFIX = "*"


class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError(f"read past end ({self.pos}+{n} > {len(self.data)})")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int: return self.take(1)[0]
    def u16(self) -> int: return struct.unpack("<H", self.take(2))[0]
    def u32(self) -> int: return struct.unpack("<I", self.take(4))[0]
    def u64(self) -> int: return struct.unpack("<Q", self.take(8))[0]
    def bool(self) -> bool: return self.u8() != 0
    def guid(self) -> uuid.UUID: return uuid.UUID(bytes_le=self.take(16))

    def string(self) -> str:
        n = self.u16()
        return self.take(n).rstrip(b"\x00").decode("utf-8", "replace") if n else ""

    def rest(self) -> bytes:
        return self.take(len(self.data) - self.pos)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def u8(self, v: int): self.buf += bytes([v & 0xFF]); return self
    def u16(self, v: int): self.buf += struct.pack("<H", v & 0xFFFF); return self
    def u32(self, v: int): self.buf += struct.pack("<I", v & 0xFFFFFFFF); return self
    def u64(self, v: int): self.buf += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF); return self
    def bool(self, v: bool): return self.u8(1 if v else 0)
    def guid(self, g: uuid.UUID): self.buf += g.bytes_le; return self
    def raw(self, b: bytes): self.buf += b; return self

    def string(self, s: str):
        b = s.encode("utf-8") + b"\x00"
        return self.u16(len(b)).raw(b)

    def bytes(self) -> bytes:
        return bytes(self.buf)


@dataclass
class Message:
    protocol: str
    is_request: bool
    call_id: int
    method: str = ""            # "Proto::Method_V1" (responses carry the "*" suffix)
    success: bool = True
    error: tuple[str, int] | None = None
    body: bytes = b""           # params (request) / outputs (response)


def parse(data: bytes) -> Message:
    r = Reader(data)
    size = r.u32()
    if size not in (len(data) - 4, len(data)):
        raise ValueError(f"RMC size field {size} does not match message length {len(data)}")
    proto = r.string()
    if r.u8() == 1:
        call_id = r.u32()
        return Message(proto, True, call_id, r.string(), body=r.rest())
    if r.u8() == 1:
        call_id = r.u32()
        return Message(proto, False, call_id, r.string(), body=r.rest())
    err = (r.string(), r.u16())
    return Message(proto, False, r.u32(), success=False, error=err)


def _finish(w: Writer) -> bytes:
    b = w.bytes()
    return struct.pack("<I", len(b)) + b


def build_request(protocol: str, call_id: int, method: str, params: bytes = b"") -> bytes:
    return _finish(Writer().string(protocol).u8(1).u32(call_id).string(method).raw(params))


def build_response(protocol: str, call_id: int, method: str, outputs: bytes = b"") -> bytes:
    return _finish(Writer().string(protocol).u8(0).u8(1).u32(call_id)
                   .string(method + RESPONSE_SUFFIX).raw(outputs))


def build_error(protocol: str, call_id: int, name: str = "Core", code: int = 2) -> bytes:
    return _finish(Writer().string(protocol).u8(0).u8(0).string(name).u16(code).u32(call_id))


def read_type_map(r: Reader) -> dict[str, int]:
    return {r.string(): r.u16() for _ in range(r.u32())}


# Quazal Variant: u8 type + value. Seen in Steep: 1 int64, 2 double, 4 String, 6 uint64 (stat
# events and stat groups). 3 bool (1 byte) and 5 DateTime (8 bytes) follow the Quazal definition
# but have not been observed yet.
V_NONE, V_INT64, V_DOUBLE, V_BOOL, V_STRING, V_DATETIME, V_UINT64 = range(7)


def read_variant(r: Reader) -> tuple[int, object]:
    t = r.u8()
    if t == V_INT64:
        return t, struct.unpack("<q", r.take(8))[0]
    if t == V_DOUBLE:
        return t, struct.unpack("<d", r.take(8))[0]
    if t == V_BOOL:
        return t, r.u8() != 0
    if t == V_STRING:
        return t, r.string()
    if t in (V_DATETIME, V_UINT64):
        return t, r.u64()
    if t == V_NONE:
        return t, None
    raise ValueError(f"unknown Variant type {t}")


def write_variant(w: Writer, t: int, value) -> Writer:
    w.u8(t)
    if t == V_INT64:
        return w.raw(struct.pack("<q", value))
    if t == V_DOUBLE:
        return w.raw(struct.pack("<d", value))
    if t == V_BOOL:
        return w.bool(value)
    if t == V_STRING:
        return w.string(value)
    if t in (V_DATETIME, V_UINT64):
        return w.u64(value)
    if t == V_NONE:
        return w
    raise ValueError(f"unknown Variant type {t}")


def quazal_datetime(t: datetime.datetime) -> int:
    """Quazal DateTime packing (verified against a captured GetGameStorageTime reply)."""
    return t.second | t.minute << 6 | t.hour << 12 | t.day << 17 | t.month << 22 | t.year << 26
