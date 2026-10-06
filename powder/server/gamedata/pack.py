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
import shutil
import zipfile
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
                        "'powder-server gamedata build <recordings> <folder>'")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format") != PACK_FORMAT:
        raise PackError(f"game-data pack format {manifest.get('format')} not supported (need {PACK_FORMAT})")
    return manifest


def verify_files(root: Path, manifest: dict) -> list[str]:
    """Names of files whose content no longer matches the manifest."""
    return [name for name, digest in manifest.get("files", {}).items()
            if not (root / name).exists() or sha256(root / name) != digest]


def check(root: Path) -> dict:
    """The manifest of a complete, unchanged pack at `root`; PackError says what is wrong otherwise."""
    manifest = read_manifest(root)
    missing = [name for name in FILES if name not in manifest.get("files", {})]
    bad = verify_files(root, manifest)
    if missing or bad:
        raise PackError(f"the game-data pack at {root} is incomplete or changed: {', '.join(missing + bad)}")
    return manifest


def zip_pack(root: Path, out: Path) -> dict:
    """Pack a checked pack into one .zip (to bring it to another PC)."""
    manifest = check(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("MANIFEST.json", *manifest["files"]):
            z.write(root / name, f"gamedata/{name}")
    return manifest


def _pack_root(folder: Path) -> Path:
    """The folder holding MANIFEST.json: `folder` itself or its single subfolder (a zipped folder)."""
    if (folder / "MANIFEST.json").exists():
        return folder
    subs = [p for p in folder.iterdir() if p.is_dir()]
    if len(subs) == 1 and (subs[0] / "MANIFEST.json").exists():
        return subs[0]
    raise PackError("this is not a game-data pack (no MANIFEST.json inside)")


def install(source: Path, target: Path) -> dict:
    """Install a pack from a .zip or a folder into `target`, replacing what is there only if the new
    pack is complete. Returns its manifest."""
    if not source.exists():
        raise PackError(f"{source} does not exist")
    staging = target.with_name(target.name + ".importing")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if source.is_dir():
            shutil.copytree(_pack_root(source), staging)
        else:
            try:
                with zipfile.ZipFile(source) as z:
                    for info in z.infolist():                   # refuse paths that leave the folder
                        p = Path(info.filename)
                        if p.is_absolute() or ".." in p.parts or p.drive:
                            raise PackError(f"unsafe path in the zip: {info.filename}")
                    z.extractall(staging)
            except zipfile.BadZipFile:
                raise PackError(f"{source.name} is not a .zip file") from None
            root = _pack_root(staging)
            if root != staging:                                 # zipped folder: lift its content up
                lifted = staging.with_name(staging.name + "2")
                shutil.rmtree(lifted, ignore_errors=True)
                root.rename(lifted)
                shutil.rmtree(staging)
                lifted.rename(staging)
        manifest = check(staging)
        old = target.with_name(target.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            target.rename(old)
        staging.rename(target)
        shutil.rmtree(old, ignore_errors=True)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
