-- Players: one row per UbiServices profile. space/app ids are global constants (constants.py).
CREATE TABLE players (
    profile_id    TEXT PRIMARY KEY,          -- GUID, lower-case
    user_id       TEXT NOT NULL,
    name          TEXT NOT NULL,             -- nameOnPlatform
    created_at    TEXT NOT NULL,
    settings_json TEXT NOT NULL DEFAULT '{}' -- per-player extras (future use)
);

-- UbiServices inventory: owned items and stackable quantities (credits are an item too).
CREATE TABLE inventory (
    profile_id      TEXT NOT NULL REFERENCES players(profile_id) ON DELETE CASCADE,
    item_id         TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    expiration_date TEXT,
    last_modified   TEXT NOT NULL,
    PRIMARY KEY (profile_id, item_id)
);

-- NEX PlayerStats2 values: group "player_profile" holds level/XP/medals. value = raw 8 bytes
-- (type 1 = int64 LE, type 2 = float64 LE), exactly as sent on the wire.
CREATE TABLE stats (
    profile_id TEXT NOT NULL REFERENCES players(profile_id) ON DELETE CASCADE,
    group_name TEXT NOT NULL,
    key        TEXT NOT NULL,
    type       INTEGER NOT NULL,
    value      BLOB NOT NULL,
    PRIMARY KEY (profile_id, group_name, key)
);

-- Audit trail of every inventory change (purchases, rewards, admin grants).
CREATE TABLE ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    ts         TEXT NOT NULL,
    offer_id   TEXT,
    item_id    TEXT NOT NULL,
    delta      INTEGER NOT NULL,
    balance    INTEGER NOT NULL,          -- quantity after the change
    reason     TEXT NOT NULL              -- "purchase", "reward", "grant", "seed", ...
);
CREATE INDEX ledger_player ON ledger(profile_id, id);

-- Small key/value store for server- or player-scoped settings and plugin data.
CREATE TABLE kv (
    scope      TEXT NOT NULL,             -- "server", a profile_id, or "plugin:<name>"
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
