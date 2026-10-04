"""Recorded UbiServices/Olympus HTTPS replies (assets/fixtures.json, made by tools/dump_fixtures.py).

Entries: {method, host, path (incl. query), status, content_type, body_b64}. Lookup tiers:
  exact      METHOD host path?query
  stripped   METHOD host path            (query ignored)
  normalized METHOD host path-with-{id}  (GUIDs replaced)
The looser tiers are refused for collections whose answer depends on the query (paged/filtered
lists such as the offers catalogue): replaying another page there would be silently wrong.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Paths where only an exact (query-included) match is acceptable.
QUERY_SENSITIVE = (re.compile(r"/v1/spaces/[^/]+/offers$"),)


def normalize(path: str) -> str:
    return GUID.sub("{id}", path)


@dataclass(frozen=True)
class HttpFixture:
    method: str
    host: str
    path: str
    status: int
    content_type: str
    body: bytes

    def json(self):
        return json.loads(self.body)


class HttpFixtures:
    def __init__(self, entries: list[HttpFixture]):
        self.entries = entries
        self.exact: dict[str, HttpFixture] = {}
        self.stripped: dict[str, HttpFixture] = {}
        self.normalized: dict[str, HttpFixture] = {}
        for e in entries:
            base = e.path.split("?", 1)[0]
            self.exact[f"{e.method} {e.host} {e.path}"] = e
            self.stripped.setdefault(f"{e.method} {e.host} {base}", e)
            self.normalized.setdefault(f"{e.method} {e.host} {normalize(base)}", e)

    @classmethod
    def load(cls, path: Path) -> "HttpFixtures":
        if not path.exists():
            return cls([])
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([HttpFixture(e["method"], e["host"].lower(), e["path"], e["status"],
                                e.get("content_type") or "application/json",
                                base64.b64decode(e["body_b64"])) for e in raw])

    def lookup(self, method: str, host: str, path_qs: str) -> tuple[HttpFixture, str] | None:
        """-> (fixture, tier) or None. `path_qs` is the raw path including the query string."""
        host = host.lower()
        base = path_qs.split("?", 1)[0]
        hit = self.exact.get(f"{method} {host} {path_qs}")
        if hit:
            return hit, "exact"
        if any(p.search(base) for p in QUERY_SENSITIVE):
            return None
        hit = self.stripped.get(f"{method} {host} {base}")
        if hit:
            return hit, "stripped"
        hit = self.normalized.get(f"{method} {host} {normalize(base)}")
        return (hit, "normalized") if hit else None

    def find(self, method: str, path_pattern: str) -> list[HttpFixture]:
        """All recordings whose method matches and whose path (without query) matches the regex."""
        rx = re.compile(path_pattern)
        return [e for e in self.entries if e.method == method and rx.search(e.path.split("?", 1)[0])]
