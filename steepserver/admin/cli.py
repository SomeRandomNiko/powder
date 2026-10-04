"""Admin commands (safe to run while the server is running; SQLite WAL).

  steepserver admin players
  steepserver admin inventory [PLAYER] [--item ITEM_ID]
  steepserver admin grant [PLAYER] (ITEM_ID|credits) QUANTITY
  steepserver admin stats [PLAYER] [--key KEY] [--set KEY=VALUE]
  steepserver admin ledger [PLAYER] [-n N]
  steepserver admin reseed [PLAYER]
  steepserver admin offers TEXT
PLAYER defaults to the configured default player.
"""
from __future__ import annotations

import argparse

from ..config import Config
from ..constants import CREDITS_ITEM
from ..services import Services
from ..services.stats import LEVEL_KEY, stat_value


def add_parser(sub) -> None:
    p = sub.add_parser("admin", help="inspect and edit players")
    a = p.add_subparsers(dest="admin_cmd", required=True)
    a.add_parser("players", help="list players")
    q = a.add_parser("inventory", help="show a player's inventory")
    q.add_argument("player", nargs="?")
    q.add_argument("--item")
    q = a.add_parser("grant", help="add (or with a negative number remove) items or credits")
    q.add_argument("player", nargs="?")
    q.add_argument("item", help="item id or 'credits'")
    q.add_argument("quantity", type=int)
    q = a.add_parser("stats", help="show or change statistics (level etc.)")
    q.add_argument("player", nargs="?")
    q.add_argument("--key")
    q.add_argument("--set", metavar="KEY=VALUE")
    q = a.add_parser("ledger", help="recent inventory changes")
    q.add_argument("player", nargs="?")
    q.add_argument("-n", type=int, default=20)
    q = a.add_parser("reseed", help="reset a player to the recorded state")
    q.add_argument("player", nargs="?")
    q = a.add_parser("offers", help="search the store catalogue by name")
    q.add_argument("text")


def _grant_args(args) -> None:
    # "grant ITEM QTY" (default player) parses ITEM into `player` and QTY into `item`: shift them.
    if args.admin_cmd == "grant" and args.item.lstrip("-").isdigit() and args.player:
        args.player, args.item, args.quantity = None, args.player, int(args.item)


def run(args, config: Config) -> int:
    _grant_args(args)
    s = Services.open(config)
    pid = (getattr(args, "player", None) or s.default_player_id()).lower()
    cmd = args.admin_cmd

    if cmd == "players":
        for p in s.repos.players.all():
            print(f"{p.profile_id}  {p.name:24s} level {s.stats.get(p.profile_id, LEVEL_KEY)}  "
                  f"credits {s.economy.credits(p.profile_id)}  since {p.created_at}")
    elif cmd == "inventory":
        rows = s.repos.inventory.all(pid)
        if args.item:                                    # full id or a prefix of it
            hits = [r for r in rows if r.item_id.startswith(args.item.lower())]
            for r in hits:
                print(f"{r.item_id}  quantity {r.quantity}  (modified {r.last_modified})")
            if not hits:
                print("not owned")
        else:
            print(f"{len(rows)} items, {s.economy.credits(pid)} credits")
    elif cmd == "grant":
        item = CREDITS_ITEM if args.item.lower() == "credits" else args.item
        s.accounts.player(pid)
        c = s.economy.grant(pid, item, args.quantity, "admin")
        print(f"{c.item_id}: {c.before} -> {c.after}")
    elif cmd == "stats":
        if args.set:
            key, _, value = args.set.partition("=")
            s.stats.set(pid, key, float(value) if "." in value else int(value))
        for row in s.stats.group(pid, "player_profile"):
            if not args.key or args.key in row.key:
                print(f"{row.key:48s} {stat_value(row)}")
    elif cmd == "ledger":
        for e in s.repos.ledger.recent(pid, args.n):
            print(f"{e['ts']}  {e['reason']:10s} {e['item_id']}  {e['delta']:+d} -> {e['balance']}"
                  + (f"  offer {e['offer_id']}" if e["offer_id"] else ""))
    elif cmd == "reseed":
        s.reseed(pid)
        print(f"reseeded {pid}: {s.economy.credits(pid)} credits, level {s.stats.get(pid, LEVEL_KEY)}")
    elif cmd == "offers":
        text = args.text.lower()
        for o in s.assets.catalog.by_id.values():
            if text in (o.get("nameId") or "").lower():
                cost = {i["itemId"][:8]: i["quantity"] for i in o.get("items", ())}
                print(f"{o['offerId']}  {o.get('type'):11s} {o.get('nameId')}  {cost}")
    s.db.close()
    return 0
