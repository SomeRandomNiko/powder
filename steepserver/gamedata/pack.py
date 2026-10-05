"""The game-data pack on disk.

  gamedata/
    MANIFEST.json            pack version, game build, sha256 of every file
    http.json                static game HTTPS replies (fixtures format)
    offers.json              the store catalogue: {"offers": [...]}
    actions.json             Ubisoft Club action definitions (no player state)
    rmc.json                 static NEX replies (fixtures-rmc format)
    connect/http.json        static Ubisoft Connect HTTPS replies (fixtures format)
    connect/demux.json       {"patch_info": b64, "products": [b64 product entry, ...], "init_fields": {...}}
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

PACK_FORMAT = 1
FILES = ("http.json", "offers.json", "actions.json", "rmc.json", "connect/http.json", "connect/demux.json")


class PackError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(root: Path, info: dict) -> dict:
    manifest = {"format": PACK_FORMAT, **info,
                "files": {name: sha256(root / name) for name in FILES if (root / name).exists()}}
    (root / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def read_manifest(root: Path) -> dict:
    path = root / "MANIFEST.json"
    if not path.exists():
        raise PackError(f"no game-data pack at {root} (MANIFEST.json missing); build one with "
                        "'steepserver gamedata build <recordings> <folder>'")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != PACK_FORMAT:
        raise PackError(f"game-data pack format {manifest.get('format')} not supported (need {PACK_FORMAT})")
    return manifest


def verify_files(root: Path, manifest: dict) -> list[str]:
    """Names of files whose content no longer matches the manifest."""
    return [name for name, digest in manifest.get("files", {}).items()
            if not (root / name).exists() or sha256(root / name) != digest]


@dataclass
class ConnectProducts:
    """Steep's entries in Ubisoft Connect's owned-games reply."""
    patch_info: bytes | None = None
    products: list[bytes] = field(default_factory=list)        # encoded product messages

    @classmethod
    def load(cls, path: Path) -> "ConnectProducts":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(patch_info=base64.b64decode(raw["patch_info"]) if raw.get("patch_info") else None,
                   products=[base64.b64decode(p) for p in raw.get("products", [])])
