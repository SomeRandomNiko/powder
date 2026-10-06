"""Small shared helpers."""
from __future__ import annotations

import datetime


def iso_now(delta_s: float = 0) -> str:
    """UbiServices timestamp, e.g. 2026-10-04T17:23:02.892Z"""
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delta_s)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
