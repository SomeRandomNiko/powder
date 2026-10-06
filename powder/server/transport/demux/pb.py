"""Minimal protobuf wire format: enough to read and write Ubisoft Connect's demux messages.

No schema compiler: a message is a list of fields `(number, value)`; value is an int (varint),
bytes (length-delimited: strings, sub-messages) or a list of fields (sub-message, encoded on the
fly). Fixed32/64 fields are kept as raw bytes with their wire type so a decode/encode round trip is
lossless.
"""
from __future__ import annotations

from typing import Iterable, Union

VARINT, FIXED64, LEN, FIXED32 = 0, 1, 2, 5

Value = Union[int, bytes, str, list]
Field = tuple  # (number, value) or (number, value, wire_type)


class DecodeError(ValueError):
    pass


def read_varint(b: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(b):
            raise DecodeError("truncated varint")
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        if x < 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode(b: bytes) -> list[tuple[int, int | bytes, int]]:
    """-> [(number, value, wire_type)]; value is int for varints, bytes otherwise."""
    out = []
    i = 0
    while i < len(b):
        key, i = read_varint(b, i)
        num, wt = key >> 3, key & 7
        if num == 0:
            raise DecodeError("field number 0")
        if wt == VARINT:
            v, i = read_varint(b, i)
        elif wt == LEN:
            n, i = read_varint(b, i)
            if i + n > len(b):
                raise DecodeError("truncated length-delimited field")
            v, i = b[i:i + n], i + n
        elif wt == FIXED64:
            v, i = b[i:i + 8], i + 8
        elif wt == FIXED32:
            v, i = b[i:i + 4], i + 4
        else:
            raise DecodeError(f"unsupported wire type {wt}")
        if i > len(b):
            raise DecodeError("truncated field")
        out.append((num, v, wt))
    return out


def try_decode(b: bytes) -> list | None:
    try:
        return decode(b) if b else []
    except DecodeError:
        return None


def encode(fields: Iterable[Field]) -> bytes:
    out = bytearray()
    for f in fields:
        num, value = f[0], f[1]
        wt = f[2] if len(f) > 2 else None
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            out += varint(num << 3 | VARINT) + varint(value)
            continue
        if isinstance(value, str):
            value = value.encode()
        elif isinstance(value, list):
            value = encode(value)
        if wt in (FIXED32, FIXED64):
            out += varint(num << 3 | wt) + value
        else:
            out += varint(num << 3 | LEN) + varint(len(value)) + value
    return bytes(out)


def first(fields: list, num: int, default=None):
    for f in fields:
        if f[0] == num:
            return f[1]
    return default


def text(fields: list, num: int, default: str = "") -> str:
    v = first(fields, num)
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else default


def replace(fields: list, num: int, value) -> list:
    """Copy of `fields` with field `num` set to `value` (appended if absent)."""
    out, done = [], False
    for f in fields:
        if f[0] == num and not done:
            out.append((num, value))
            done = True
        else:
            out.append(f)
    if not done:
        out.append((num, value))
    return out


# ---- length-prefixed framing (outer stream and the per-service sub-streams) -----------------------
def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + payload


def unframe(data: bytes) -> bytes:
    """The payload of one framed message (service data carries exactly one)."""
    if len(data) < 4 or int.from_bytes(data[:4], "big") != len(data) - 4:
        raise DecodeError("bad frame length")
    return data[4:]
