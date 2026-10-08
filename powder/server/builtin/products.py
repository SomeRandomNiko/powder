"""Steep's owned-games entries for Ubisoft Connect, without a game-data pack.

Connect only starts a game it finds in the player's owned games. The entries' ids and numbers come from
product_table.py; their product configuration (YAML, field 17) is written here, with only what Connect
needs to start Steep: how to launch it (Steam, or Steep.exe from Connect's install folder), its app and
space ids, no cloud saves, and the achievement definitions file if the server has one. Names, images and
links of the real configuration are left out (Connect's library then shows less).
"""
from __future__ import annotations

from ..constants import APP_ID, SPACE_ID
from ..transport.demux import pb
from .product_table import KINDS, PRODUCTS

PRODUCT_CONFIG = 17
STEAM_APP_ID = 460920

_COMMON = """\
  installer:
    game_identifier: Steep
  digital_distribution:
    version: 0
  cloud_saves: {cloud_saves}
{achievements}  app_id: {app_id}
  space_id: {space_id}
"""

CONNECT_GAME = """\
version: 2.0
root:
  name: Steep
  start_game:
    online:
      overlay_required: yes
      overlay_supported: yes
      executables:
      - shortcut_name: Steep
        path:
          relative: steep.exe
        working_directory:
          register: HKEY_LOCAL_MACHINE\\SOFTWARE\\Ubisoft\\Launcher\\Installs\\{product_id}\\InstallDir
""" + _COMMON + "localizations: {{}}\n"

STEAM_GAME = """\
version: 2.0
root:
  name: Steep
  start_game:
    steam:
      overlay_required: yes
      overlay_supported: yes
      steam_app_id: {steam_app_id}
      steam_installation_status_register: HKEY_LOCAL_MACHINE\\SOFTWARE\\Valve\\Steam\\InstallPath
      game_installation_status_register: HKEY_CURRENT_USER\\SOFTWARE\\Valve\\Steam\\Apps\\{steam_app_id}\\Installed
  third_party_platform:
    name: Steam
""" + _COMMON + "localizations: {{}}\n"

ADDON = "version: 2.0\nroot:\n  name: {name}\n  is_visible: {visible}\n{ulc}localizations: {{}}\n"


def _yaml(product_id: int, fields: list, cloud_saves: bool, achievements_file: str | None) -> bytes:
    kind = KINDS.get(product_id, "none")
    if kind == "game":
        steam = pb.first(fields, 7) == 3                       # target partner: Steam
        achievements = (f"  uplay:\n    achievements: {achievements_file}\n    achievements_sync_id: STP\n"
                        if achievements_file else "")
        text = (STEAM_GAME if steam else CONNECT_GAME).format(
            product_id=product_id, steam_app_id=STEAM_APP_ID, cloud_saves="yes" if cloud_saves else "no",
            achievements=achievements, app_id=APP_ID, space_id=SPACE_ID)
    elif kind == "dlc":
        text = ADDON.format(name=f"Steep add-on {product_id}", visible="yes", ulc="")
    elif kind == "ulc":
        text = ADDON.format(name="", visible="no", ulc="  is_ulc: yes\n")
    else:
        return b""
    return text.replace("\n", "\r\n").encode()


def entries(cloud_saves: bool = False, achievements_file: str | None = None) -> list[bytes]:
    """The encoded product messages of Steep, its Steam entry and its add-ons."""
    out = []
    for fields in PRODUCTS:
        pid = pb.first(fields, 1)
        config = _yaml(pid, fields, cloud_saves, achievements_file)
        out.append(pb.encode([(n, config if n == PRODUCT_CONFIG else v) for n, v in fields]))
    return out
