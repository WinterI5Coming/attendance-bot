-- Excuse requests: prior reports for lateness or absence.
-- The request text is kept private and should only be shown in ephemeral
-- command responses or direct officer workflows.
CREATE TABLE excuse_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id TEXT NOT NULL,

    member_id INTEGER NOT NULL,

    target_date TEXT NOT NULL,

    reason TEXT NOT NULL,

    expected_time TEXT,

    status TEXT NOT NULL
        CHECK (
            status IN (
                'PENDING',
                'APPROVED',
                'AUTO_APPROVED',
                'REJECTED',
                'CANCELLED'
            )
        ),

    requested_at TEXT NOT NULL,

    decided_by_discord_id TEXT,

    decided_at TEXT,

    rejection_reason TEXT,

    cancelled_at TEXT,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (member_id)
        REFERENCES members(id)
        ON DELETE RESTRICT
);


CREATE INDEX idx_excuse_requests_guild_date
ON excuse_requests (
    guild_id,
    target_date
);


CREATE INDEX idx_excuse_requests_guild_status_date
ON excuse_requests (
    guild_id,
    status,
    target_date
);


CREATE INDEX idx_excuse_requests_member_date
ON excuse_requests (
    member_id,
    target_date
);


CREATE INDEX idx_excuse_requests_status_requested
ON excuse_requests (
    status,
    requested_at
);


-- A member may have only one active request for a given guild-local date.
-- REJECTED and CANCELLED requests are historical and allow a new request.
CREATE UNIQUE INDEX idx_excuse_requests_active_unique
ON excuse_requests (
    guild_id,
    member_id,
    target_date
)
WHERE status IN (
    'PENDING',
    'APPROVED',
    'AUTO_APPROVED'
);
