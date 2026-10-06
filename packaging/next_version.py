"""The next release tag (semantic versioning), used by the release workflow:

    git tag --list | python packaging/next_version.py patch|minor|major
prints e.g. v0.1.1. The first release is v0.1.0 whatever the bump. Only tags of the form vX.Y.Z count.
"""
from __future__ import annotations

import re
import sys

FIRST = (0, 1, 0)
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def next_tag(tags: list[str], bump: str) -> str:
    if bump not in ("major", "minor", "patch"):
        raise ValueError(f"bump must be major, minor or patch, not {bump!r}")
    versions = [tuple(int(x) for x in m.groups()) for t in tags if (m := TAG.match(t.strip()))]
    if not versions:
        return "v%d.%d.%d" % FIRST
    major, minor, patch = max(versions)
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"v{major}.{minor}.{patch}"


if __name__ == "__main__":
    print(next_tag(sys.stdin.read().split(), sys.argv[1]))
