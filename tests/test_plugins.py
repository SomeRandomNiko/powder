"""Plugin loading, hooks and events."""
import textwrap

from steepserver.plugins.loader import PluginManager
from steepserver.transport.nex import rmc_codec as rmc
from steepserver.transport.nex.dispatch import RmcDispatcher

PLUGIN = textwrap.dedent('''
    def setup(api):
        seen = api.services.__dict__.setdefault("_test_seen", [])
        api.on("purchase", lambda profile_id, offer_id, changes: seen.append(offer_id))
        async def time_hook(ctx, params):
            return b"\\x2a" * 8
        api.before_rmc("GameStorageProtocol::GetGameStorageTime_V1", time_hook)
        api.save("loaded", True)
''')
BROKEN = "def setup(api):\n    raise RuntimeError('boom')\n"


async def test_plugin_hooks_events_and_isolation(tmp_path, services, player):
    (tmp_path / "good.py").write_text(PLUGIN)
    (tmp_path / "broken.py").write_text(BROKEN)              # must not stop the others
    (tmp_path / "_template.py").write_text(BROKEN)           # underscore files are skipped
    pm = PluginManager(services)
    pm.load_dir(tmp_path)
    assert pm.loaded == ["good"]
    assert services.repos.kv.get("plugin:good", "loaded") is True

    msg = rmc.parse(rmc.build_request("GameStorageProtocol", 3, "GameStorageProtocol::GetGameStorageTime_V1"))
    reply = rmc.parse(await RmcDispatcher(services, pm).dispatch(None, msg))
    assert reply.body == b"\x2a" * 8

    await services.events.emit("purchase", profile_id=player, offer_id="X", changes=[])
    assert services._test_seen == ["X"]
