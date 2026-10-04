"""Offer catalogue: every offer seen in any recorded GET /v1/spaces/{space}/offers page.

Filter semantics, verified against all 44 recorded query families (tests/test_catalog.py):
  tags=A,B   offer has ALL listed tags          type=X   offer.type == X
  itemIds=.. offer grants ANY listed item       offset/limit   paging over first-seen order
An offer's items[] carry SIGNED quantities: a shop offer is +1 item / -price credits, a reward
offer is +credits (+ item). See services/economy.py.
"""
from __future__ import annotations

import json

from .http import HttpFixtures


class OfferCatalog:
    def __init__(self, offers: list[dict]):
        self.by_id: dict[str, dict] = {}
        for o in offers:
            self.by_id.setdefault(o["offerId"], o)

    @classmethod
    def from_fixtures(cls, fixtures: HttpFixtures) -> "OfferCatalog":
        offers: list[dict] = []
        for e in fixtures.find("GET", r"/v1/spaces/[^/]+/offers$"):
            try:
                offers.extend(json.loads(e.body).get("offers", []))
            except ValueError:
                continue
        return cls(offers)

    def __len__(self) -> int:
        return len(self.by_id)

    def get(self, offer_id: str) -> dict | None:
        return self.by_id.get(offer_id)

    def query(self, *, tags: str | None = None, type: str | None = None, item_ids: str | None = None,
              offset: int = 0, limit: int = 100) -> list[dict]:
        want_tags = set(filter(None, (tags or "").split(",")))
        want_items = set(filter(None, (item_ids or "").split(",")))
        out = []
        for o in self.by_id.values():
            if type and o.get("type") != type:
                continue
            if want_tags and not want_tags <= set(o.get("tags", ())):
                continue
            if want_items and not want_items & {i["itemId"] for i in o.get("items", ())}:
                continue
            out.append(o)
        return out[offset:offset + limit]
