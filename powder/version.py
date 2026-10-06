"""Powder's version (semantic versioning). The release build replaces this value with the tag's version.

  major  launcher and server are not compatible with the previous major, or data needs manual migration
  minor  new features
  patch  fixes
"""
__version__ = "0.0.0+dev"


def major(version: str) -> int | None:
    """The major number of 'X.Y.Z' (also 'vX.Y.Z' or with a '+build' suffix); None if it isn't one."""
    head = version.lstrip("v").split("+")[0].split(".")[0]
    return int(head) if head.isdigit() else None


def compatible(a: str, b: str) -> bool:
    """Launcher and server work together when their major versions match (dev builds always do)."""
    if "+dev" in a or "+dev" in b:
        return True
    return major(a) is not None and major(a) == major(b)
