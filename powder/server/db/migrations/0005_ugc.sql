-- Player-made content (Steep's UGC service: photos, trails/replays, ...): the item as the game describes
-- it; its files (data, thumbnail) are kept in data/ugc/.
CREATE TABLE ugc (
    ugc_id        TEXT PRIMARY KEY,
    type_id       INTEGER NOT NULL,
    world_id      INTEGER,
    profile_id    TEXT NOT NULL,
    pub_date      TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tags_json     TEXT NOT NULL DEFAULT '[]',
    extra_json    TEXT NOT NULL DEFAULT '{}',   -- any other field the game sent when creating it
    hidden        INTEGER NOT NULL DEFAULT 0,
    downloads     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ugc_type_world ON ugc(type_id, world_id);

CREATE TABLE ugc_likes (
    ugc_id     TEXT NOT NULL REFERENCES ugc ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    PRIMARY KEY (ugc_id, profile_id)
);
