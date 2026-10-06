"""powder-server gamedata build <recordings> <pack> | verify [<pack>] | zip <file.zip> | install <zip|folder>"""
from __future__ import annotations

from pathlib import Path

from . import build as builder
from . import pack


def add_parser(sub) -> None:
    p = sub.add_parser("gamedata", help="build or check the game-data pack")
    g = p.add_subparsers(dest="gamedata_cmd", required=True)
    b = g.add_parser("build", help="build a pack from your recordings (allow-list + privacy check)")
    b.add_argument("recordings", type=Path)
    b.add_argument("pack", type=Path)
    v = g.add_parser("verify", help="check a pack's manifest and file hashes")
    v.add_argument("pack", type=Path, nargs="?")
    z = g.add_parser("zip", help="pack the game-data pack into one .zip (to bring it to another PC)")
    z.add_argument("out", type=Path)
    z.add_argument("--pack", type=Path, help="pack folder (default: the configured one)")
    i = g.add_parser("install", help="install a game-data pack from a .zip or folder")
    i.add_argument("source", type=Path)


def run(args, config) -> int:
    if args.gamedata_cmd in ("zip", "install"):
        try:
            if args.gamedata_cmd == "zip":
                m = pack.zip_pack(args.pack or config.gamedata_dir, args.out)
                print(f"wrote {args.out} (pack built {m.get('built')})")
            else:
                m = pack.install(args.source, config.gamedata_dir)
                print(f"installed the game-data pack (built {m.get('built')}) into {config.gamedata_dir}")
            return 0
        except pack.PackError as e:
            print(e)
            return 1
    if args.gamedata_cmd == "build":
        report = builder.build(args.recordings, args.pack)
        for name, n in report.kept.items():
            print(f"  {name:20s} {n}")
        if report.findings:
            print(f"PRIVACY CHECK FAILED ({len(report.findings)} findings); nothing written:")
            for f in report.findings[:40]:
                print("  " + f)
            return 1
        print(f"pack written to {args.pack} (privacy check passed)")
        return 0
    root = args.pack or config.gamedata_dir
    try:
        manifest = pack.read_manifest(root)
    except pack.PackError as e:
        print(e)
        return 1
    bad = pack.verify_files(root, manifest)
    print(f"pack {root}: built {manifest.get('built')}, {manifest.get('sandbox')}, "
          f"{'OK' if not bad else 'CHANGED: ' + ', '.join(bad)}")
    return 1 if bad else 0
