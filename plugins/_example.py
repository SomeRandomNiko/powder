"""Example plugin (skipped because the file name starts with "_"; copy it to e.g. my_plugin.py).

See steepserver/plugins/api.py for the full API.
"""
from steepserver.constants import CREDITS_ITEM


def setup(api):
    api.log.info("example plugin loaded (API v%d)", api.version)

    def on_purchase(profile_id, offer_id, changes):
        for c in changes:
            if c.item_id == CREDITS_ITEM:
                api.log.info("%s spent/earned %+d credits (now %d)", profile_id[:8], c.delta, c.after)

    api.on("purchase", on_purchase)

    # Example of answering a NEX call yourself: return output bytes, or None to let the server decide.
    # def my_time(ctx, params):
    #     return None
    # api.before_rmc("GameStorageProtocol::GetGameStorageTime_V1", my_time)
