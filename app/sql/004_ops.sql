-- ---------------------------------------------------------------------------
-- Who has opened the portal.
--
-- First-party and self-hosted: no third-party script, no advertising network,
-- nothing leaves this database. A visitor is recognised by a random id in a
-- cookie this app sets itself. The IP address is never stored, only a salted
-- hash of it, so two visits can be told apart without the address being
-- readable here or recoverable from a database dump.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.visit (
    visit_id    bigserial PRIMARY KEY,
    visitor_id  text        NOT NULL,   -- random, from a cookie this app sets
    seen_at     timestamptz NOT NULL DEFAULT now(),
    tag         text,                   -- from ?from=... so a shared link says who
    path        text,
    referrer    text,
    user_agent  text,
    ip_hash     text                    -- salted hash; the address itself is not kept
);

CREATE INDEX IF NOT EXISTS visit_seen_idx    ON ops.visit (seen_at DESC);
CREATE INDEX IF NOT EXISTS visit_visitor_idx ON ops.visit (visitor_id);
CREATE INDEX IF NOT EXISTS visit_tag_idx     ON ops.visit (tag);

-- One row per visitor: when they first and last opened it, and how often.
CREATE OR REPLACE VIEW ops.visitor AS
SELECT visitor_id,
       MAX(tag) FILTER (WHERE tag IS NOT NULL) AS tag,
       COUNT(*)                                AS visits,
       MIN(seen_at)                            AS first_seen,
       MAX(seen_at)                            AS last_seen,
       MAX(user_agent)                         AS user_agent,
       MAX(referrer) FILTER (WHERE referrer IS NOT NULL) AS referrer
FROM ops.visit
GROUP BY visitor_id;

-- One row per labelled link, so "has the investor opened it yet" has an answer.
CREATE OR REPLACE VIEW ops.link AS
SELECT tag,
       COUNT(DISTINCT visitor_id) AS people,
       COUNT(*)                   AS visits,
       MIN(seen_at)               AS first_opened,
       MAX(seen_at)               AS last_opened
FROM ops.visit
WHERE tag IS NOT NULL
GROUP BY tag;
