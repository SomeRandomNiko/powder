"""Build a game-data pack from recordings of real sessions (your own captures).

    steepserver gamedata build <recordings dir> <pack dir>

Only allow-listed, game-wide replies are copied; Ubisoft Club actions are rewritten to drop player state.
The privacy check (privacy.py) must then find no identifying value from the rest of the recordings in
the pack, or the build fails and nothing is written.
"""
from __future__ import annotations

import base64
import datetime
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..constants import APP_ID, SANDBOX, SPACE_ID
from ..transport.demux import pb
from . import pack, privacy

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ID = f"(?:{GUID}|global|all)"

# (method, path regex without query): game-wide configuration only, nothing under a profile or user
GAME_ALLOW = [
    ("GET", rf"/v2/applications/{GUID}/configuration"),
    ("GET", rf"/v1/spaces/{GUID}/configs/primarystore"),
    ("GET", rf"/v4/spaces/{GUID}/configs/events"),
    ("GET", rf"/v1/spaces/{GUID}/items/all"),
    ("GET", r"/v2/spaces/global/ubiconnect/challenges/api/legacy/empty"),
]
CONNECT_ALLOW = [
    ("GET", rf"/v1/applications/{ID}/configuration"),
    ("GET", rf"/v1/applications/{ID}/parameters"),
    ("GET", r"/v1/applications/global/webservices/ubisoftplus/vault/products"),
    ("GET", r"/v1/mdm/products"),
    ("GET", rf"/v1/spaces/{ID}/parameters"),
    ("GET", rf"/v1/spaces/{ID}/global/ubiconnect/games/api"),
    ("GET", r"/v1/spaces/global/ubiconnect/games/api"),
    ("GET", rf"/v1/spaces/{ID}/global/ubiconnect/configcache/api/connecturls"),
    ("GET", rf"/v1/spaces/{ID}/global/ubiconnect/challenges/api"),
    ("GET", rf"/v1/spaces/{ID}/global/ubiconnect/rewards/api"),
    ("GET", r"/v1/spaces/global/ubiconnect/economy/api/metaprogression"),
    ("GET", r"/v4/spaces/global/ubiconnect/configcache/api/(preauth|postauth)"),
    ("GET", rf"/v4/spaces/{GUID}/configs/events"),
    ("GET", rf"/v2/spaces/{GUID}/configs/news/tags"),
    ("GET", r"/orbit/uplay_launcher_14_0/free_games/latest\.txt"),
    ("GET", r"/orbit/uplay_launcher_3_0/assets/[0-9a-f]+\.zip"),   # achievement definitions (Connect)
]
RMC_ALLOW = {"GameStorageProtocol::GetGameParamaters_V1", "SteepTournamentProtocol::FetchSeasonInfo_V1"}
STEEP_PRODUCTS = {3279, 3280}                      # Steam entry + Ubisoft product; their add-ons follow
OFFERS_PATH = re.compile(rf"/v1/spaces/{GUID}/offers$")
ACTIONS_PATH = re.compile(rf"/v1/profiles/{GUID}/actions$")
ACTION_PLAYER_FIELDS = ("profileId", "value")       # per-player state in an action


class BuildError(RuntimeError):
    pass


@dataclass
class Report:
    kept: dict[str, int] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)


def _allowed(rules, method: str, path: str) -> bool:
    base = path.split("?", 1)[0]
    return any(m == method and re.fullmatch(rx, base) for m, rx in rules)


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _body(e: dict):
    return json.loads(base64.b64decode(e["body_b64"]))


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _offers(game: list[dict]) -> tuple[list[dict], set[int]]:
    offers, seen, used = [], set(), set()
    for i, e in enumerate(game):
        if e["method"] == "GET" and OFFERS_PATH.search(e["path"].split("?", 1)[0]):
            used.add(i)
            for o in _body(e).get("offers", []):
                if o["offerId"] not in seen:
                    seen.add(o["offerId"])
                    offers.append(o)
    return offers, used


def _actions(game: list[dict]) -> tuple[list[dict], set[int]]:
    """Ubisoft Club action definitions, without whose they are and how far that player got."""
    by_id, used = {}, set()
    for i, e in enumerate(game):
        if e["method"] == "GET" and ACTIONS_PATH.search(e["path"].split("?", 1)[0]):
            used.add(i)
            for a in _body(e).get("actions", []):
                by_id.setdefault(a["id"], {k: v for k, v in a.items() if k not in ACTION_PLAYER_FIELDS})
    return list(by_id.values()), used


def _products(demux: dict) -> list[bytes]:
    """Steep's entries (and its add-ons) from the recorded owned-games reply; the rest of the library
    stays out."""
    replies = demux.get("services", {}).get("ownership_service", {}).get("2", [])
    if not replies:
        return []
    reply = base64.b64decode(replies[0])
    init = pb.decode(pb.first(pb.decode(pb.first(pb.decode(reply), 1)), 2))
    entries = [v for n, v, _ in pb.decode(pb.first(init, 2))]
    decoded = {pb.first(pb.decode(v), 1): v for v in entries}
    wanted = set(STEEP_PRODUCTS)
    for pid in STEEP_PRODUCTS:
        if pid in decoded:
            wanted.update(v for n, v, _ in pb.decode(decoded[pid]) if n == 16)
    return [decoded[p] for p in sorted(wanted) if p in decoded]


def build(recordings: Path, out: Path) -> Report:
    game = _load(recordings / "fixtures.json", [])
    rmc = _load(recordings / "fixtures-rmc.json", {})
    connect = _load(recordings / "connect-fixtures.json", [])
    demux = _load(recordings / "demux.json", {})
    if not game:
        raise BuildError(f"no recordings in {recordings} (fixtures.json missing)")
    report = Report()
    tmp = out.with_name(out.name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "connect").mkdir(parents=True)

    offers, offer_idx = _offers(game)
    actions, action_idx = _actions(game)
    game_kept = {i for i, e in enumerate(game) if _allowed(GAME_ALLOW, e["method"], e["path"])}
    _write(tmp / "http.json", [game[i] for i in sorted(game_kept)])
    _write(tmp / "offers.json", {"offers": offers})
    _write(tmp / "actions.json", {"actions": actions})
    _write(tmp / "rmc.json", {m: rows for m, rows in rmc.items() if m in RMC_ALLOW})
    connect_kept = {i for i, e in enumerate(connect) if _allowed(CONNECT_ALLOW, e["method"], e["path"])}
    _write(tmp / "connect" / "http.json", [connect[i] for i in sorted(connect_kept)])
    products = _products(demux)
    _write(tmp / "connect" / "demux.json", {
        "patch_info": demux.get("patch_info"),
        "products": [base64.b64encode(p).decode() for p in products]})
    report.kept = {"http.json": len(game_kept), "offers.json": len(offers), "actions.json": len(actions),
                   "rmc.json": len(RMC_ALLOW & set(rmc)), "connect/http.json": len(connect_kept),
                   "connect/products": len(products)}

    # privacy: identifying values from everything that was NOT kept must not appear in the pack
    markers = (privacy.markers_from_http(game, game_kept | offer_idx)
               | privacy.markers_from_http(connect, connect_kept)
               | privacy.markers_from_rmc(rmc, RMC_ALLOW)
               | privacy.markers_from_demux(demux))
    report.findings = privacy.check(tmp, markers, allowed=_game_ids(game_kept, game, connect_kept, connect))
    if report.findings:
        shutil.rmtree(tmp)
        return report
    pack.write_manifest(tmp, {"built": datetime.date.today().isoformat(), "sandbox": SANDBOX,
                              "app_id": APP_ID, "space_id": SPACE_ID, "contents": report.kept})
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    return report


def _game_ids(game_kept, game, connect_kept, connect) -> set[str]:
    """Ids that are game-wide by definition: those in the URL paths of the kept (allow-listed) requests,
    e.g. the game's app and space ids. They also occur inside personal replies, which is fine."""
    ids = {APP_ID, SPACE_ID}
    for idx, entries in ((game_kept, game), (connect_kept, connect)):
        for i in idx:
            ids.update(x.lower() for x in re.findall(GUID, entries[i]["path"]))
    return ids
