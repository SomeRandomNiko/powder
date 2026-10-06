"""Discovers and runs plugins (see api.py). Files starting with "_" are skipped (templates)."""
from __future__ import annotations

import fnmatch
import importlib.util
import inspect
import logging
from pathlib import Path

from .api import PluginAPI

log = logging.getLogger("powder.plugins")


class PluginManager:
    def __init__(self, services):
        self.services = services
        self.http_hooks: list[tuple] = []         # (METHOD, glob, fn, plugin)
        self.rmc_hooks: dict[str, list[tuple]] = {}
        self.loaded: list[str] = []

    def load_dir(self, folder: Path) -> None:
        cfg = self.services.config.plugins
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.py")):
            name = path.stem
            if name.startswith("_"):
                continue
            if name in cfg.disabled or ("*" not in cfg.enabled and name not in cfg.enabled):
                log.info("plugin %s disabled by config", name)
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"powder_plugin_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                setup = getattr(module, "setup", None)
                if setup is None:
                    log.warning("plugin %s has no setup(api); skipped", name)
                    continue
                setup(PluginAPI(self, name))
                self.loaded.append(name)
                log.info("plugin %s loaded", name)
            except Exception:  # noqa: BLE001
                log.exception("plugin %s failed to load", name)

    async def run_before_http(self, request):
        for method, glob, fn, name in self.http_hooks:
            if method not in ("*", request.method) or not fnmatch.fnmatchcase(request.path, glob):
                continue
            result = await _call(fn, name, request)
            if result is not None:
                return result
        return None

    async def run_before_rmc(self, method: str, ctx, params: bytes) -> bytes | None:
        for fn, name in self.rmc_hooks.get(method, ()):
            result = await _call(fn, name, ctx, params)
            if result is not None:
                return result
        return None


async def _call(fn, plugin: str, *args):
    try:
        result = fn(*args)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception:  # noqa: BLE001
        log.exception("plugin %s hook %r failed (ignored)", plugin, fn)
        return None
