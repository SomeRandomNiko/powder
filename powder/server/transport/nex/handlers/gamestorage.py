"""GameStorageProtocol and the season info the game asks for at start (no game-data pack needed)."""
from __future__ import annotations

import datetime
import time

from ..rmc_codec import Reader, Writer, quazal_datetime


def get_game_storage_time(ctx, r: Reader) -> bytes:
    """outputs (reader FUN_7ff661946de0): 8-byte Quazal DateTime = the server's current time."""
    return Writer().u64(quazal_datetime(datetime.datetime.now(datetime.timezone.utc))).bytes()


def get_game_parameters(ctx, r: Reader) -> bytes:
    """GetGameParamaters (sic): the real server answered 8 zero bytes for every request (no parameters)."""
    return bytes(8)


def fetch_season_info(ctx, r: Reader) -> bytes:
    """SteepTournamentProtocol::FetchSeasonInfo_V1: no running season (32 zero bytes) and the server's
    current time (u32 unix seconds), as the real server answered."""
    return bytes(32) + Writer().u32(int(time.time())).bytes()


HANDLERS = {
    "GameStorageProtocol::GetGameStorageTime_V1": get_game_storage_time,
    "GameStorageProtocol::GetGameParamaters_V1": get_game_parameters,
    "SteepTournamentProtocol::FetchSeasonInfo_V1": fetch_season_info,
}
