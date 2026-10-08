"""Ubisoft Connect's in-game overlay settings (configcache/api/postauth), without a pack.

The overlay (Shift+F2) is a frame that loads its parts (profile, notifications, friends, store...) from
Ubisoft's CDN; these settings list the parts and switch features. Unlike Connect's main window, the
overlay does not cache them, and without them it fails to open (dolphin-040). These are written for
Powder: valid, with no parts listed, so the overlay opens but stays empty. Logs and metrics are off.
"""
from __future__ import annotations

REMOTELOG = "https://public-ubiservices.ubi.com/v1/profiles/me/remotelog"
LOG_CATEGORIES = """api focus input shell streaming webSocket gameLaunch navigation unexpected performance
shellPlugin kinderGarden apiValidation configuration serviceWorker uncategorized authentication initialization
hostCommunication microAppLifecycle navigationHistory playerNotification""".split()


def application_settings() -> dict:
    return {
        "microApplications": [],
        "accessMenu": [],
        "lastUpdate": {"lastUpdateDate": "2016-12-01T00:00:00.000Z", "isMandatory": False},
        "manifests": [],
        "notifications": {"default": "topRight", "overrides": []},
        "serviceUrls": {"remoteLogs": REMOTELOG,
                        "metricsCollector": "https://public-ubiservices.ubi.com/v1/spaces/all/global/sre/opentelemetry/api/metrics",
                        "websocketNotifications": "wss://public-ws-ubiservices.ubi.com/v2/websocket"},
        "feedback": {"feedbackButtonUrl": "", "ubisoftHelpButtonUrl": "", "reportBugButtonUrl": "",
                     "discordServerButtonUrl": ""},
        "remoteLogs": {"url": REMOTELOG, "maxTextLength": 32768, **{c: "None" for c in LOG_CATEGORIES}},
        "cacheParameters": {"useConfigService": True},
        "features": {"metrics": False, "replaceRouterReducer": True, "replaceSwitchMicroApp": True,
                     "disableMicroAppPreload": True, "rainbow6ThemingEnabled": False,
                     "achievementNotification": True, "anonymousPreAuthenticatedLogging": 0.0},
        "metricCategoryFilters": {"p0": False, "p1": False, "p2": False, "p3": False, "p4": False,
                                  "performance": False},
    }


def postauth(query) -> dict | None:
    """{application id: settings} for the applications asked for (`applications=` a comma list)."""
    apps = [a for a in query.get("applications", "").split(",") if a]
    return {a: application_settings() for a in apps} or None
