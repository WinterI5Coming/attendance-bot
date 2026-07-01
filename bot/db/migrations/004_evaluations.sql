-- Officer evaluations are stored separately from the append-only score ledger.
-- The score_events rows remain the source of truth for totals; this table keeps
-- the operational metadata needed to cancel an evaluation with a reversal event.
CREATE TABLE evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id TEXT NOT NULL,
    member_id INTEGER NOT NULL,
    score_event_id INTEGER,
    evaluator_discord_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('ACTIVE', 'CANCELLED')),
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    cancelled_by_discord_id TEXT,
    cancellation_reason TEXT,
    reversal_score_event_id INTEGER,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (member_id)
        REFERENCES members(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (score_event_id)
        REFERENCES score_events(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (reversal_score_event_id)
        REFERENCES score_events(id)
        ON DELETE RESTRICT,

    CHECK (score BETWEEN -5 AND 5 AND score != 0),
    UNIQUE (score_event_id),
    UNIQUE (reversal_score_event_id)
);

CREATE INDEX idx_evaluations_guild_member_created
ON evaluations (guild_id, member_id, created_at);

CREATE INDEX idx_evaluations_guild_status_created
ON evaluations (guild_id, status, created_at);

CREATE INDEX idx_evaluations_evaluator_created
ON evaluations (evaluator_discord_id, created_at);
