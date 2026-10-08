"""Ubisoft Connect's in-game overlay settings (configcache/api/postauth), without a pack.

The overlay (Shift+F2) is a frame (a "shell" from Ubisoft's CDN) that loads its parts ("micro apps":
profile, friends, store...) from the addresses these settings list. Unlike Connect's main window, the
overlay does not cache them: without them it fails to open (dolphin-040), and with no part listed too
(dolphin-029). These are written for Powder: a single part of our own, served by this server
(/_powder/overlay/powder.js), and no Ubisoft parts. Achievement pop-ups are drawn by the frame itself
(features.achievementNotification). Logs and metrics are off.

The part's contract (from the shell's code): a SystemJS module whose default export is a class with a
static apiVersion, constructed as new App(shellApi, appConfig) with appName equal to the settings entry's,
and lifecycle methods mount(el) -> true, beforeActivate() -> true, beforeDeactivate(), beforeUnmount().
"""
from __future__ import annotations

from ..constants import APP_ID, SPACE_ID

REMOTELOG = "https://public-ubiservices.ubi.com/v1/profiles/me/remotelog"
LOG_CATEGORIES = """api focus input shell streaming webSocket gameLaunch navigation unexpected performance
shellPlugin kinderGarden apiValidation configuration serviceWorker uncategorized authentication initialization
hostCommunication microAppLifecycle navigationHistory playerNotification""".split()


POWDER_APP = "powder"
POWDER_SPACE = "b0a5c0de-0000-4000-8000-000000000001"      # made up: the part's own space
POWDER_PART_PATH = "/_powder/overlay/powder.js"

# The Powder part: plain JavaScript, no dependencies (served with CORS, as the CDN does)
POWDER_PART_JS = """\
System.register([], function (_export) {
  return {
    execute: function () {
      class Powder {
        constructor(shellApi, appConfig) {
          this.shellApi = shellApi; this.appConfig = appConfig; this.appName = "powder";
        }
        async mount(el) {
          el.innerHTML =
            '<div style="font-family:sans-serif;color:#fff;padding:32px;max-width:560px">' +
            '<h1 style="font-size:28px;margin:0 0 12px">Powder</h1>' +
            '<p style="font-size:16px;line-height:1.5;opacity:.85;margin:0">' +
            'You are playing on a Powder server. Your progress and achievements are stored there; ' +
            'Ubisoft\\'s overlay pages (profile, friends, store) are not available.</p></div>';
          return true;
        }
        async beforeActivate() { return true; }
        async beforeDeactivate() { return { confirmationRequired: false, applicationData: null }; }
        async beforeUnmount() {}
        async apiUpdated(shellApi) { this.shellApi = shellApi; }
        static appIsAvailable() { return Promise.resolve(true); }
      }
      Powder.apiVersion = "1.1.0";
      _export("default", Powder);
    }
  };
});
"""


def powder_part(host: str) -> dict:
    return {"appName": POWDER_APP, "spaceId": POWDER_SPACE, "default": True,
            "bundleUrl": f"https://{host}{POWDER_PART_PATH}", "manifestUrl": "", "label": "Powder", "iconUrl": "",
            "parents": [{"parentAppName": None, "order": 0, "defaultPlacement": "main"}],
            "remoteLogsConfiguration": {"url": REMOTELOG, "maxTextLength": 32768, "uncategorized": "None"}}


def application_settings(host: str = "public-ubiservices.ubi.com") -> dict:
    return {
        "microApplications": [powder_part(host)],
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


# Where the overlay frame lives ({shellBaseUrl} is Connect's CDN address it knows itself)
OVERLAY_URL = ("{shellBaseUrl}/overlay/default/?microApp={microApp}&microAppParams={microAppParams}&spaceId={spaceId}"
               "&applicationId={applicationId}&profileId={profileId}&platform={platform}&locale={locale}"
               "&ubiTicket={ubiTicket}&env={environment}&deviceType=desktop&playerSessionId={playerSessionId}"
               "&country={country}")


def client_parameters() -> dict:
    """GET /v1/applications/{Connect's app id}/parameters: Connect's client settings. Only the overlay's
    address (without it the overlay fails with dolphin-029); account-security settings are left out."""
    return {"parameters": {"uplayPc-clientConfigs": {"fields": {"ubisoftConnectOverlayUrlV2": OVERLAY_URL},
                                                     "relatedPopulation": None}}}


UBI = "https://public-ubiservices.ubi.com"
CONNECT_SPACE = "e17be87d-2996-4f3b-97c4-19bb2dae2933"
CONNECT_APP = "f68a4bb5-608a-4ff2-8123-be8ef797e0a6"
SHELL_SPACE, SHELL_APP = "0a706b37-4b88-4437-b8f4-4ed2458c9518", "20adeb9c-6dad-404e-af1e-b12b4594e86e"


def connect_urls() -> dict:
    """GET .../ubiconnect/configcache/api/connecturls from Connect itself (not its browser): where its
    services and its window (shell) live. Hostnames and URL templates only. Without it, Connect waits 30 s
    at every start before falling back to its cached copy."""
    return {
        "demuxUrl": "dmx.upc.ubisoft.com",
        "shellUrl": ("https://{env}connect.cdn.ubisoft.com/overlay/default/bootstrap.html?env={environment}"
                     "&isStandalone=true&platform={platform}&deviceType=desktop&locale={locale}"
                     f"&spaceId={SHELL_SPACE}&applicationId={SHELL_APP}&ticket={{ubiTicket}}"
                     "&sessionId={playerSessionId}&profileId={profileId}&country={country}&region={region}"
                     "&ownershipGroup={ownershipGroup}&deviceId={deviceId}&deviceName={deviceName}"
                     "&isOffline={isOffline}"),
        "cdnBaseUrl": "https://static3.cdn.ubi.com",
        "shellBaseUrl": "https://{env}connect.cdn.ubisoft.com",
        "serviceLayerUrl": "https://servicelayer{env}.ecom.ubi.com",
        "storeSessionsUrl": "https://store-sessions{env}.ecom.ubi.com",
        "ubiServicesBaseUrl": "https://{env}public-ubiservices.ubi.com",
        "ubiServicesWSSBaseUrl": "wss://{env}public-ws-ubiservices.ubi.com",
        "ubiServicesNotificationBaseUrl": "https://{env}public-im-ubiservices.ubi.com",
        "eventsUrl": f"{UBI}/v3/profiles/me/events",
        "overlayUrl": OVERLAY_URL,
        "profilesUrl": f"{UBI}/{{version}}/profiles",
        "usersCreateUrl": f"{UBI}/{{version}}/users",
        "configsEventsUrl": f"{UBI}/{{version}}/spaces/{{spaceId}}/configs/events",
        "visitorEventsUrl": f"{UBI}/v5/spaces/{CONNECT_SPACE}/serviceEvents",
        "metricsCollectorUrl": f"{UBI}/v1/spaces/all/global/sre/opentelemetry/api/metrics",
        "profilesEntitiesUrl": f"{UBI}/{{version}}/profiles/{{profileId}}/entities",
        "crashReportUploadUrl": "",
        "allProfilesEntitiesUrl": f"{UBI}/{{version}}/profiles/entities",
        "remoteLogsCollectorUrl": f"{UBI}/v1/spaces/all/global/sre/opentelemetry/api/logs",
        "gameActivationSupportUrl": "",
        "usersValidateCreationUrl": f"{UBI}/{{version}}/users/validatecreation",
        "visitorMetricsCollectorUrl": f"{UBI}/v1/applications/{CONNECT_APP}/global/sre/opentelemetry/anon/metrics",
        "steamGameActivationSupportUrl": "",
        "visitorRemoteLogsCollectorUrl": f"{UBI}/v1/applications/{CONNECT_APP}/global/sre/opentelemetry/anon/logs",
    }


def native_parameters(groups: str) -> dict:
    """GET /v1/spaces/{id}/parameters?parameterGroups=... from Connect itself (no cached copy; with an error
    it waits 30 s at start). Empty groups, except the address of Connect's window."""
    out = {}
    if "connect-clientConfigs" in groups.split(","):
        out["connect-clientConfigs"] = {"fields": {"shellUrl": connect_urls()["shellUrl"]}, "relatedPopulation": None}
    return {"parameters": out}


def game_card(space_id: str) -> dict | None:
    """Connect's card for Steep (GET .../ubiconnect/games/api): ids and name only, no texts or images.
    The overlay waits for it (about 4 s without one) before the game logs in."""
    if space_id != SPACE_ID:
        return None
    return {"spaceId": SPACE_ID, "installmentSpaceId": SPACE_ID, "displayName": "Steep", "displayDescription": "",
            "platforms": [{"type": "PC", "displayName": "PC", "applicationId": APP_ID}], "siblingGames": [],
            "installmentDefault": True, "ageRating": None, "releaseAt": None, "releaseStatusDisplayName": None,
            "imageUrls": {k: None for k in ("lowThumbnail", "highThumbnail", "background", "lowBoxArt",
                                            "highBoxArt", "gameLogoFlat", "gameLogoColor", "gameAvatar")},
            "linkUrls": {k: None for k in ("facebook", "twitter", "instagram", "reddit", "website")}}


def game_cards(query) -> dict | None:
    cards = [c for c in (game_card(s) for s in query.get("spaceIds", "").split(",")) if c]
    return {"games": cards} if cards else None


def postauth(query) -> dict | None:
    """{application id: settings} for the applications asked for (`applications=` a comma list)."""
    apps = [a for a in query.get("applications", "").split(",") if a]
    return {a: application_settings() for a in apps} or None
