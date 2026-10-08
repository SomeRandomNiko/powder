"""Admin commands (safe to run while the server is running; SQLite WAL).

  powder-server admin players
  powder-server admin inventory [PLAYER] [--item ITEM_ID]
  powder-server admin grant [PLAYER] (ITEM_ID|credits) QUANTITY
  powder-server admin stats [PLAYER] [--key KEY] [--set KEY=VALUE]
  powder-server admin ledger [PLAYER] [-n N]
  powder-server admin events [PLAYER] [-n N]        stat uploads from the game (XP, medals, unlocks)
  powder-server admin scores [PLAYER]               personal best per challenge
  powder-server admin entities [PLAYER]             profile entities (rider card, favorites)
  powder-server admin achievements [PLAYER] [--all] earned achievements (--all: also the open ones)
  powder-server admin import RECORDINGS_DIR [--replace]   your own recorded progress -> database (once)
  powder-server admin reset [PLAYER]                start over as a new player
  powder-server admin offers TEXT
  powder-server admin trust (status|install|remove)   local CA in Windows' trusted roots (Ubisoft Connect)
PLAYER defaults to the configured default player.
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from .. import trust as trustmod
from ..config import Config
from ..constants import CREDITS_ITEM
from ..services import Services
from ..services.stats import LEVEL_KEY, stat_value
from ..tls import ensure_pki


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
    q = a.add_parser("events", help="recent stat uploads from the game")
    q.add_argument("player", nargs="?")
    q.add_argument("-n", type=int, default=20)
    q = a.add_parser("scores", help="personal best per challenge")
    q.add_argument("player", nargs="?")
    q = a.add_parser("entities", help="profile entities (rider card, favorites)")
    q.add_argument("player", nargs="?")
    q = a.add_parser("achievements", help="earned achievements (as Ubisoft Connect reported them)")
    q.add_argument("player", nargs="?")
    q.add_argument("--all", action="store_true", help="also list the ones not earned yet")
    q = a.add_parser("import", help="import your own recorded progress into the database (once)")
    q.add_argument("recordings", type=Path)
    q.add_argument("--replace", action="store_true", help="overwrite that player if it exists")
    q = a.add_parser("reset", help="wipe a player: they start again as a new player")
    q.add_argument("player", nargs="?")
    q = a.add_parser("offers", help="search the store catalogue by name")
    q.add_argument("text")
    q = a.add_parser("trust", help="the local CA in Windows' trusted roots (needed by Ubisoft Connect)")
    q.add_argument("action", choices=("status", "install", "remove"))


def _grant_args(args) -> None:
    # "grant ITEM QTY" (default player) parses ITEM into `player` and QTY into `item`: shift them.
    if args.admin_cmd == "grant" and args.item.lstrip("-").isdigit() and args.player:
        args.player, args.item, args.quantity = None, args.player, int(args.item)


def trust(action: str, config: Config) -> int:
    """Ubisoft Connect checks certificates through Windows, so our local CA must be a trusted root
    (see powder/server/trust.py)."""
    pki = ensure_pki(config.data_dir / "tls")
    try:
        if action == "install":
            trustmod.install(pki.ca_pem)
        elif action == "remove":
            trustmod.remove(pki.ca_pem)
        trusted = trustmod.is_trusted(pki.ca_pem)
    except trustmod.TrustError as e:
        print(e)
        return 1
    print(f"local CA {pki.ca_thumbprint}: {'TRUSTED' if trusted else 'not trusted'} ({pki.ca_pem})")
    for other in trustmod.other_tool_roots():
        print(f"note: a {other} CA is also trusted (capture tooling); remove it when no longer needed")
    return 0


def run(args, config: Config) -> int:
    _grant_args(args)
    if args.admin_cmd == "trust":
        return trust(args.action, config)
    s = Services.open(config)
    pid = (getattr(args, "player", None) if args.admin_cmd != "import" else None) or s.default_player_id()
    pid = pid.lower()
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
    elif cmd == "events":
        for e in s.repos.stat_events.recent(pid, args.n):
            print(f"{e['ts']}  {e['name']:32s} {e['fields_json']}")
    elif cmd == "scores":
        for e in s.repos.challenge_scores.all(pid):
            print(f"{e['challenge_id']:48s} {e['best_score']:>12g}  ({e['direction']}, {e['updated']})")
    elif cmd == "achievements":
        names = s.assets.achievement_names()            # Steep's, from the game-data pack
        products = s.repos.achievements.products(pid) or {"STP:PC": 0}
        for product, n in products.items():
            earned = s.repos.achievements.all(pid, product)
            steep = product == "STP:PC" and names
            print(f"{product}: {n} earned" + (f" of {len(names)}" if steep else ""))
            for ach_id, ts in earned.items():
                name = f"  {names[ach_id][0]}" if steep and ach_id in names else ""
                print(f"  {ach_id:4d}  {datetime.datetime.fromtimestamp(ts, datetime.UTC):%Y-%m-%d %H:%M} UTC{name}")
            if steep and args.all:
                print("  not yet earned:")
                for ach_id, (name, text) in names.items():
                    if ach_id not in earned:
                        print(f"  {ach_id:4d}  {name} - {text}")
    elif cmd == "entities":
        for e in s.entities.for_profile(pid, limit=1000):
            print(f"{e['entityId']}  {e['type']:20s} rev {e['revision']:<4} {e['lastModified']}  "
                  f"obj keys: {', '.join(list(e.get('obj') or {})[:6])}")
    elif cmd == "import":
        from ..services.importer import ImportError_, import_recordings
        try:
            r = import_recordings(s, args.recordings, replace=args.replace)
        except ImportError_ as e:
            print(e)
            s.db.close()
            return 1
        print(f"imported {r.name} ({r.profile_id}): {r.items} items, {r.credits} credits, {r.stats} stats, "
              f"{r.entities} entities, {r.achievements} achievements; now the default player")
    elif cmd == "reset":
        s.reset(pid)
        print(f"reset {pid}: starts as a new player on the next login")
    elif cmd == "offers":
        text = args.text.lower()
        for o in s.assets.catalog.by_id.values():
            if text in (o.get("nameId") or "").lower():
                cost = {i["itemId"][:8]: i["quantity"] for i in o.get("items", ())}
                print(f"{o['offerId']}  {o.get('type'):11s} {o.get('nameId')}  {cost}")
    s.db.close()
    return 0
