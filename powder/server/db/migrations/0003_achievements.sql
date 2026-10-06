-- Achievements a player earned, per product (Ubisoft Connect's key, e.g. "STP:PC"), as Connect reports
-- them (ach_frontend kind 4). Earliest unlock time wins; nothing is ever removed by Connect.
CREATE TABLE achievements (
    profile_id     TEXT NOT NULL,
    product        TEXT NOT NULL,
    achievement_id INTEGER NOT NULL,
    earned_at      INTEGER NOT NULL,          -- unix time, as Connect sends it
    PRIMARY KEY (profile_id, product, achievement_id)
);
