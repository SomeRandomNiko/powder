"""Inventory and store (UbiServices). Credits are inventory item CREDITS_ITEM.

Buying in the shop AND receiving challenge rewards are the same call:
    POST /v1/profiles/{id}/inventory  {"spaceId", "offerId", "offerRevision"}
The offer (from the recorded catalogue) lists items with SIGNED quantities, e.g. a shop offer
+1 skis / -12000 credits, a gold-medal reward +600 credits / +1 item. Applying an offer adds each
delta to the stored inventory; the reply reports quantityBefore and the new total `quantity`
(shape copied from real replies, see captures/2026-10-04_19-17-35_full/inventory-flows.jsonl).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from ..config import Config
from ..constants import CREDITS_ITEM, SPACE_ID
from ..core.events import EventBus
from ..db.repos import Repos
from ..fixtures.catalog import OfferCatalog
from ..util import iso_now

log = logging.getLogger("powder.economy")


class EconomyError(Exception):
    def __init__(self, status: int, error_code: int, message: str):
        super().__init__(message)
        self.status, self.error_code, self.message = status, error_code, message

    def body(self) -> dict:
        """UbiServices-style error body."""
        return {"errorCode": self.error_code, "message": self.message, "httpCode": self.status}


@dataclass(frozen=True)
class Change:
    item_id: str
    delta: int
    before: int
    after: int


class EconomyService:
    def __init__(self, config: Config, repos: Repos, catalog: OfferCatalog, events: EventBus):
        self.config, self.repos, self.catalog, self.events = config, repos, catalog, events

    # ---- reads ---------------------------------------------------------------------------------
    def inventory_reply(self, profile_id: str) -> dict:
        """GET /v1/profiles/{id}/inventory body (items sorted by itemId, descending, like the real one)."""
        return {"items": [{"itemId": row.item_id, "quantity": row.quantity,
                           "expirationDate": row.expiration_date, "lastModified": row.last_modified,
                           "expirationDetails": None}
                          for row in self.repos.inventory.all(profile_id)]}

    def credits(self, profile_id: str) -> int:
        return self.repos.inventory.quantity(profile_id, CREDITS_ITEM)

    # ---- writes --------------------------------------------------------------------------------
    def apply_offer(self, profile_id: str, offer_id: str, revision: int | None = None) -> tuple[dict, list[Change]]:
        """Purchase / reward. -> (reply body, changes). Raises EconomyError."""
        offer = self.catalog.get(offer_id)
        if offer is None:
            raise EconomyError(404, 1001, f"offer {offer_id} not found")
        if revision is not None and revision != offer.get("revision"):
            log.warning("offer %s: client revision %s, catalogue revision %s (accepted)",
                        offer_id, revision, offer.get("revision"))

        toggles = self.config.toggles(profile_id)
        deltas: list[tuple[str, int, int]] = []          # (item, requested delta, applied delta)
        for item in offer.get("items", ()):
            requested = int(item["quantity"])
            applied = requested
            if item["itemId"] == CREDITS_ITEM:
                if requested < 0 and toggles.free_purchases:
                    applied = 0
                elif requested > 0 and offer.get("type") == "progression":
                    applied = round(requested * toggles.reward_credit_multiplier)
            deltas.append((item["itemId"].lower(), requested, applied))

        reason = "purchase" if offer.get("type") == "shop" else (offer.get("type") or "offer")
        now = iso_now()
        changes: list[Change] = []
        with self.repos.db.transaction():
            for item_id, _requested, applied in deltas:
                before = self.repos.inventory.quantity(profile_id, item_id)
                after = before + applied
                if after < 0:
                    raise EconomyError(400, 1002, f"not enough of item {item_id} "
                                                  f"(have {before}, need {-applied})")
                changes.append(Change(item_id, applied, before, after))
            for c in changes:
                self.repos.inventory.set(profile_id, c.item_id, c.after, last_modified=now)
                self.repos.ledger.add(profile_id, c.item_id, c.delta, c.after, reason, offer_id)

        reply = {
            "primaryStoreItemQuantity": 0,
            "primaryStoreItemIsConsumable": False,
            "items": [{"quantityBefore": c.before, "spaceId": SPACE_ID, "profileId": profile_id,
                       "itemId": c.item_id, "quantity": c.after, "expirationDate": None,
                       "lastModified": now, "expirationDetails": []} for c in changes],
            "additionalDetails": [{"receivedItemId": item_id, "requestedItemId": item_id,
                                   "receivedQuantity": applied, "requestedQuantity": requested,
                                   "code": None, "description": None}
                                  for item_id, requested, applied in deltas],
            "transactionId": str(uuid.uuid4()),
        }
        log.info("%s %s offer %s (%s): %s", profile_id[:8], reason, offer_id, offer.get("nameId"),
                 ", ".join(f"{c.item_id[:8]} {c.before}->{c.after}" for c in changes))
        return reply, changes

    def grant(self, profile_id: str, item_id: str, delta: int, reason: str = "grant") -> Change:
        """Admin/plugin: add (or remove) a quantity of an item."""
        item_id = item_id.lower()
        with self.repos.db.transaction():
            before = self.repos.inventory.quantity(profile_id, item_id)
            after = before + delta
            if after < 0:
                raise EconomyError(400, 1002, f"would go negative ({before} {delta:+})")
            self.repos.inventory.set(profile_id, item_id, after)
            self.repos.ledger.add(profile_id, item_id, delta, after, reason)
        return Change(item_id, delta, before, after)
