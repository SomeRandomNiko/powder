"""GameStorageProtocol. GetGameParamaters (sic) is answered from the recording."""
from __future__ import annotations

import datetime

from ..rmc_codec import Reader, Writer, quazal_datetime


def get_game_storage_time(ctx, r: Reader) -> bytes:
    """outputs (reader FUN_7ff661946de0): 8-byte Quazal DateTime = the server's current time."""
    return Writer().u64(quazal_datetime(datetime.datetime.now(datetime.timezone.utc))).bytes()


HANDLERS = {
    "GameStorageProtocol::GetGameStorageTime_V1": get_game_storage_time,
}
