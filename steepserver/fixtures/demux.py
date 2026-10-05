"""Recorded Ubisoft Connect demux replies (recordings/demux.json, made by tools/dump_demux.py).

{
  "client_version": 13368,
  "patch_info": "<b64 Rsp.patch_info body>",
  "service_requests": {"utility_service": ["<b64 Rsp.service_rsp body>", ...], ...},
  "services": {"ownership_service": {"<request kind>": ["<b64 reply message>", ...], ...}, ...}
}

Service messages are matched by (service, request kind) = the field number that says which request
it is; the n-th request of a kind gets the n-th recorded reply (the last one repeats). Request ids
are rewritten by the caller (transport/demux/services.py).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path


def _b(s: str) -> bytes:
    return base64.b64decode(s)


@dataclass
class DemuxFixtures:
    client_version: int | None = None
    patch_info: bytes | None = None
    service_requests: dict[str, list[bytes]] = field(default_factory=dict)
    services: dict[str, dict[int, list[bytes]]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "DemuxFixtures":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            client_version=raw.get("client_version"),
            patch_info=_b(raw["patch_info"]) if raw.get("patch_info") else None,
            service_requests={k: [_b(x) for x in v] for k, v in raw.get("service_requests", {}).items()},
            services={svc: {int(kind): [_b(x) for x in replies] for kind, replies in kinds.items()}
                      for svc, kinds in raw.get("services", {}).items()})

    def reply(self, service: str, kind: int, nth: int) -> bytes | None:
        replies = self.services.get(service, {}).get(kind)
        if not replies:
            return None
        return replies[min(nth, len(replies) - 1)]

    def service_request(self, service: str, nth: int) -> bytes | None:
        replies = self.service_requests.get(service)
        if not replies:
            return None
        return replies[min(nth, len(replies) - 1)]

    def __len__(self) -> int:
        return sum(len(r) for kinds in self.services.values() for r in kinds.values())
