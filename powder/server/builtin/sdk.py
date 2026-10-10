"""The Ubisoft services SDK configuration (GET /v{n}/applications/{app id}/configuration), without a pack.

The game and Ubisoft Connect fetch their SDK configuration at start. It switches the SDK's features on
(a feature missing from `featuresSwitches` is off: with none, the game stops right after its login) and
tells it where each service lives (`gatewayResources`: URL templates on the hostnames Powder redirects).
This one is written for Powder: the features and services the game uses, default timeouts, no CDN
links; the peer-to-peer (storm) servers are filled in per server (https/app.py storm_config). Connect gets the same minus the game's features.
"""
from __future__ import annotations

from ..constants import APP_ID, CONNECT_APP_ID, SPACE_ID

CONNECT_SPACE_ID = "e17be87d-2996-4f3b-97c4-19bb2dae2933"
LEGACY_EMPTY = "https://{env}public-ubiservices.ubi.com/v2/spaces/global/ubiconnect/challenges/api/legacy/empty"

# SDK features the game uses (names of the SDK's feature switches)
FEATURES = """
ApplicationMetadata ApplicationUsed ClubActions ClubApplication ClubBadges ClubChallenges ClubConditions
ClubDynamicPanel ClubFriends ClubRewards ClubUnits ClubXps Connection ContentFiltering CreateSession Entities
EntitiesProfile EntitiesSpace Event Everything ExtendSession FixAccountIssues FriendsLookup FriendsProposal
FriendsRequest FriendsSuggestions HttpClient KeepAlive LeaderboardMe LeaderboardProfiles LeaderboardSpaces
Localization Messaging MobileExtensionProfilesExternal MobileExtensionUsersManagement News
NotificationRequestConnections NotificationSend NotificationSendBatch NotificationSendNoBroker
NotificationWebsocket Populations PopulationsAutomaticFetch PopulationsSendInPlayerStart Presence PrimaryStore
PrimaryStoreAutomaticFetch PrimaryStoreSendEvent Profiles ProfilesExternal SecondaryStore
SendPopulationsInPlayerStart SendPrimaryStoreEvent Socialfeed SocialfeedRead SocialfeedWrite Stats Uplay
UplayFriends UplayLaunch UplayPassport UplayWin UplayWinActions UplayWinRewards Users UsersCreateAndLink
UsersLegalOptins UsersManagement WebSocketClient
""".split()

# service name -> (host prefix, path after /{version}, version); "msr-" services live on msr-public-ubiservices
SERVICES = {
    "all_connections": ("msr-", "profiles/connections", 1),
    "all_profiles/applications": ("msr-", "profiles/applications", 2),
    "all_profiles/entities": ("", "profiles/entities", 2),
    "all_profiles/stats": ("msr-", "profiles/stats", 1),
    "all_spaces/entities": ("", "spaces/entities", 2),
    "all_spaces/items": ("", "spaces/items", 1),
    "all_spaces/offers": ("", "spaces/offers", 1),
    "all_walls": ("msr-", "profiles/wall", 1),
    "applicationsMetadata": ("", "applications", 1),
    "certificates": ("msr-", "certificates", 1),
    "configs/events": ("msr-", "spaces/{spaceId}/configs/events", 4),
    "connections": ("msr-", "profiles/{profileId}/connections", 1),
    "events": ("msr-", "profiles/{profileId}/events", 2),
    "friends": ("msr-", "profiles/me/friends", 2),
    "news": ("msr-", "profiles/me/news", 1),
    "populations": ("", "profiles/me/populations", 1),
    "profiles": ("msr-", "profiles", 3),
    "profiles/actions": ("msr-", "profiles/{profileId}/actions", 1),
    "profiles/applications": ("msr-", "profiles/me/applications", 2),
    "profiles/entities": ("", "profiles/{profileId}/entities", 2),
    "profiles/inventory": ("", "profiles/{profileId}/inventory", 1),
    "profiles/inventory/expiredDetails": ("", "profiles/{profileId}/inventory/expiredDetails", 1),
    "profiles/leaderboard": ("", "profiles/ranks", 1),
    "profiles/me/events": ("msr-", "profiles/me/events", 3),
    "profiles/me/inventory/primarystore": ("", "profiles/me/inventory/primarystore", 1),
    "profiles/me/leaderboard": ("", "profiles/me/ranks", 1),
    "profiles/notifications": ("msr-", "profiles/{profileId}/notifications", 2),
    "profiles/notificationsBatch": ("", "profiles/notifications", 2),
    "profiles/rewards": ("msr-", "profiles/{profileId}/rewards", 1),
    "profiles/stats": ("", "profiles/{profileId}/stats", 1),
    "profiles/statsCard": ("msr-", "profiles/statscard", 1),
    "remote_logs": ("", "profiles/me/remotelog", 1),
    "remote_logs_custom": ("msr-", "profiles/me/remotelog", 1),
    "sandboxes": ("msr-", "spaces/{spaceId}/sandboxes", 0),
    "spaces/actions": ("msr-", "spaces/{spaceId}/actions", 1),
    "spaces/configs/primarystore": ("", "spaces/{spaceId}/configs/primarystore", 1),
    "spaces/entities": ("", "spaces/{spaceId}/entities", 2),
    "spaces/items": ("", "spaces/{spaceId}/items", 1),
    "spaces/leaderboard": ("", "spaces/{spaceId}/leaderboards", 1),
    "spaces/news": ("msr-", "spaces/news", 1),
    "spaces/offers": ("", "spaces/{spaceId}/offers", 1),
    "spaces/rewards": ("msr-", "spaces/{spaceId}/rewards", 1),
    "spaces/stats": ("msr-", "spaces/{spaceId}/communitystats", 1),
    "spaces/statsCard": ("msr-", "spaces/{spaceId}/communitystatscard", 1),
    "wall": ("msr-", "profiles/{profileId}/wall", 1),
    "wall/comments": ("msr-", "profiles/wall/{postId}/comments", 1),
    "wall/likes": ("msr-", "profiles/wall/{postId}/likes", 1),
    "wall/post": ("msr-", "profiles/wall/{postId}", 1),
}
# Ubisoft Connect's challenge services: the game expects them to answer "nothing"
EMPTY_SERVICES = {"profiles/challenges": 2, "profiles/me/communityChallenges": 1, "profiles/profileChallenges": 3,
                  "spaces/challengepools": 2, "spaces/challenges": 2, "spaces/communityChallenges": 1}


def gateway_resources() -> list[dict]:
    out = [{"name": name, "version": version,
            "url": f"https://{prefix}{{env}}public-ubiservices.ubi.com/{{version}}/{path}"}
           for name, (prefix, path, version) in SERVICES.items()]
    out += [{"name": name, "version": version, "url": LEGACY_EMPTY} for name, version in EMPTY_SERVICES.items()]
    out += [{"name": "websocket/notifications", "version": 2,
             "url": "wss://{env}public-ws-ubiservices.ubi.com/{version}/websocket"},
            {"name": "websocket/server", "version": 1, "url": "wss://{env}public-ws-ubiservices.ubi.com"}]
    return out


# app id -> (space id, game code, the application's own features on)
APPLICATIONS = {
    APP_ID: (SPACE_ID, "STP", True),                       # Steep
    CONNECT_APP_ID: (CONNECT_SPACE_ID, "AVA", False),      # Ubisoft Connect
}


def configuration(app_id: str) -> dict | None:
    known = APPLICATIONS.get(app_id.lower())
    if known is None:
        return None
    space_id, game_code, game = known
    retry = {"incrementFactorMsec": 5000, "initialDelayMsec": 5000, "maxCount": 2, "randomDelayMsec": 1000}
    return {
        "configuration": {
            "custom": {"resources": [], "featuresSwitches": []},
            "featuresSwitches": [{"name": f, "value": True} for f in (FEATURES if game else ["ApplicationUsed"])],
            "gatewayResources": gateway_resources() if game else [],
            "storm": {},
            "sdkConfig": {
                "remoteLogs": {"ubiservicesLogLevel": "None", "prodLogLevel": "None"},
                "httpSafetySleepTime": 0, "keepAliveTimeoutMin": 10, "timeoutSec": 30,
                "httpParam": {"maxCount": 2, "retryParam": retry, "timeoutParam": {"initialDelayMsec": 30000}},
                "websocketParam": {"maxCount": 1000, "retryParam": retry,
                                   "timeoutParam": {"initialDelayMsec": 30000}},
            },
            "clubServices": [],
            "platformConfig": {"uplayGameCode": game_code, "applicationId": app_id.lower(), "spaceId": space_id,
                               "platform": "PC", "environment": "prod"},
            "legacyUrls": [],
            "events": {"queues": [{"name": q, "sendPeriodMilliseconds": 30000} for q in "ABC"], "tags": []},
            "resources": [],
            "uplayServices": [],
        },
        "relatedPopulations": [],
    }
