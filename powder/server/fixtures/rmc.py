"""Recorded NEX replies (assets/fixtures-rmc.json, made by tools/make_nex_fixtures.py).

Format: {"Proto::Method_V1": [{"params_sha1", "params_len", "body": <outputs hex>}, ...]}.
Lookup: exact params match; else "uniform" when every recording of the method returned the same bytes
(usually an empty ack), since the params evidently do not matter then.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class RmcFixtures:
    def __init__(self, data: dict[str, list[dict]]):
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "RmcFixtures":
        if not path.exists():
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def lookup(self, method: str, params: bytes) -> tuple[bytes, str] | None:
        entries = self.data.get(method)
        if not entries:
            return None
        digest = hashlib.sha1(params).hexdigest()
        for e in entries:
            if e["params_sha1"] == digest:
                return bytes.fromhex(e["body"]), "exact"
        if len({e["body"] for e in entries}) == 1:
            return bytes.fromhex(entries[0]["body"]), "uniform"
        return None

    def latest(self, method: str) -> bytes | None:
        """Most recent recording of a method (captures are merged oldest -> newest)."""
        entries = self.data.get(method)
        return bytes.fromhex(entries[-1]["body"]) if entries else None
