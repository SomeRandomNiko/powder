-- Every stat event the game uploaded (PlayerStats2 PostEvents), raw: lets rules be re-applied if
-- one turns out wrong, and shows what the game reported.
CREATE TABLE stat_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    name        TEXT NOT NULL,
    fields_json TEXT NOT NULL
);
CREATE INDEX stat_events_player ON stat_events(profile_id, id);

-- Personal best per challenge from the *_public_challenge_score_event uploads.
-- direction "descending": higher is better; "ascending": lower is better.
CREATE TABLE challenge_scores (
    profile_id   TEXT NOT NULL,
    challenge_id TEXT NOT NULL,
    direction    TEXT NOT NULL,
    best_score   REAL NOT NULL,
    replay       TEXT,
    updated      TEXT NOT NULL,
    PRIMARY KEY (profile_id, challenge_id)
);

-- UbiServices profile entities: rider card (type PROFILE), favorites, saves.
CREATE TABLE profile_entities (
    entity_id     TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL,
    space_id      TEXT NOT NULL,
    type          TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    editable      INTEGER NOT NULL DEFAULT 1,
    tags_json     TEXT NOT NULL DEFAULT '[]',
    obj_json      TEXT NOT NULL DEFAULT '{}',
    extended_json TEXT,                     -- extendedStorage, when present
    revision      INTEGER NOT NULL DEFAULT 1,
    last_modified TEXT NOT NULL
);
CREATE INDEX profile_entities_player ON profile_entities(profile_id, type);
