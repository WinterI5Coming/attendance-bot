-- Stage B: late reductions and absence exemptions as append-only adjustments.

ALTER TABLE guild_settings
ADD COLUMN exempt_absence_counts_in_attendance_denominator INTEGER NOT NULL
    DEFAULT 0
    CHECK (exempt_absence_counts_in_attendance_denominator IN (0, 1));

CREATE TABLE attendance_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id TEXT NOT NULL,
    session_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    attendance_record_id INTEGER NOT NULL,
    excuse_request_id INTEGER NOT NULL,

    adjustment_type TEXT NOT NULL
        CHECK (adjustment_type IN ('LATE_REDUCTION', 'ABSENCE_EXEMPTION')),

    status TEXT NOT NULL
        CHECK (status IN ('ACTIVE', 'CANCELLED')),

    requested_reduction_seconds INTEGER
        CHECK (requested_reduction_seconds IS NULL OR requested_reduction_seconds >= 0),

    original_status TEXT NOT NULL,
    resulting_status TEXT NOT NULL,

    original_late_seconds INTEGER
        CHECK (original_late_seconds IS NULL OR original_late_seconds >= 0),

    resulting_late_seconds INTEGER
        CHECK (resulting_late_seconds IS NULL OR resulting_late_seconds >= 0),

    score_event_id INTEGER,
    reversal_score_event_id INTEGER,

    applied_by_discord_id TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    reason TEXT NOT NULL,

    cancelled_by_discord_id TEXT,
    cancelled_at TEXT,
    cancellation_reason TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    FOREIGN KEY (session_id)
        REFERENCES attendance_sessions(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (member_id)
        REFERENCES members(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (attendance_record_id)
        REFERENCES attendance_records(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (excuse_request_id)
        REFERENCES excuse_requests(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (score_event_id)
        REFERENCES score_events(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (reversal_score_event_id)
        REFERENCES score_events(id)
        ON DELETE RESTRICT,

    CHECK (
        (adjustment_type = 'LATE_REDUCTION'
            AND original_late_seconds IS NOT NULL
            AND resulting_late_seconds IS NOT NULL)
        OR
        (adjustment_type = 'ABSENCE_EXEMPTION'
            AND original_late_seconds IS NULL
            AND resulting_late_seconds IS NULL)
    ),

    CHECK (
        (status = 'ACTIVE'
            AND cancelled_at IS NULL
            AND cancelled_by_discord_id IS NULL
            AND cancellation_reason IS NULL)
        OR
        (status = 'CANCELLED'
            AND cancelled_at IS NOT NULL
            AND cancelled_by_discord_id IS NOT NULL
            AND cancellation_reason IS NOT NULL)
    ),

    UNIQUE (score_event_id),
    UNIQUE (reversal_score_event_id)
);

CREATE UNIQUE INDEX idx_attendance_adjustments_active_unique
ON attendance_adjustments (attendance_record_id, adjustment_type)
WHERE status = 'ACTIVE';

CREATE INDEX idx_attendance_adjustments_guild_member_applied
ON attendance_adjustments (guild_id, member_id, applied_at);

CREATE INDEX idx_attendance_adjustments_session_member
ON attendance_adjustments (session_id, member_id);

CREATE INDEX idx_attendance_adjustments_record
ON attendance_adjustments (attendance_record_id);

CREATE INDEX idx_attendance_adjustments_excuse
ON attendance_adjustments (excuse_request_id);

CREATE INDEX idx_attendance_adjustments_type_status
ON attendance_adjustments (adjustment_type, status);
