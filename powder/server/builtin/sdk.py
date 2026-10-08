"""The Ubisoft services SDK configuration (GET /v{n}/applications/{app id}/configuration), without a pack.

The game and Ubisoft Connect fetch their SDK configuration at start and check it against a schema (an
empty reply makes Connect report dolphin-040 "Failed retrieving configurations"). This is a minimal one
written for Powder: the application's platform ids and default timeouts, and no feature switches or
service URL overrides, so the SDK keeps its built-in defaults (which point at the redirected hostnames).
"""
from __future__ import annotations

from ..constants import APP_ID, CONNECT_APP_ID, SPACE_ID

# app id -> (space id, game code) of the applications that ask
APPLICATIONS = {
    APP_ID: (SPACE_ID, "STP"),                                                   # Steep
    CONNECT_APP_ID: ("e17be87d-2996-4f3b-97c4-19bb2dae2933", "AVA"),             # Ubisoft Connect
}


def configuration(app_id: str) -> dict | None:
    known = APPLICATIONS.get(app_id.lower())
    if known is None:
        return None
    space_id, game_code = known
    retry = {"incrementFactorMsec": 5000, "initialDelayMsec": 5000, "maxCount": 2, "randomDelayMsec": 1000}
    return {
        "configuration": {
            "custom": {"resources": [], "featuresSwitches": []},
            "featuresSwitches": [],
            "gatewayResources": [],
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
            "events": {"queues": [{"name": "A", "sendPeriodMilliseconds": 30000}], "tags": []},
            "resources": [],
            "uplayServices": [],
        },
        "relatedPopulations": [],
    }
