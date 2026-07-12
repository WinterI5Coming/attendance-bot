-- Stage A: attendance policies, date overrides, voice presence, and verification.

ALTER TABLE guild_settings
ADD COLUMN voice_verification_enabled INTEGER NOT NULL
    DEFAULT 0
    CHECK (voice_verification_enabled IN (0, 1));

ALTER TABLE guild_settings
ADD COLUMN voice_channel_ids TEXT;

ALTER TABLE guild_settings
ADD COLUMN voice_category_ids TEXT;

ALTER TABLE attendance_sessions
ADD COLUMN verification_end_at TEXT;

ALTER TABLE attendance_sessions
ADD COLUMN required_voice_seconds INTEGER;

ALTER TABLE attendance_sessions
ADD COLUMN early_leave_penalty INTEGER;

ALTER TABLE attendance_sessions
ADD COLUMN no_participation_penalty INTEGER;

CREATE TABLE attendance_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    policy_type TEXT NOT NULL
        CHECK (policy_type IN ('WEEKDAY', 'WEEKEND')),
    enabled INTEGER NOT NULL
        DEFAULT 0
        CHECK (enabled IN (0, 1)),
    start_time TEXT NOT NULL,
    late_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    verification_end_time TEXT NOT NULL,
    required_voice_minutes INTEGER NOT NULL
        CHECK (required_voice_minutes > 0),
    present_score INTEGER NOT NULL,
    late_score INTEGER NOT NULL,
    early_leave_penalty INTEGER NOT NULL,
    no_participation_penalty INTEGER NOT NULL,
    absent_score INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    UNIQUE (guild_id, policy_type),
    CHECK (start_time < late_time AND late_time < close_time AND close_time < verification_end_time)
);

CREATE TABLE attendance_date_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    attendance_date TEXT NOT NULL,
    enabled INTEGER NOT NULL
        CHECK (enabled IN (0, 1)),
    start_time TEXT NOT NULL,
    late_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    verification_end_time TEXT NOT NULL,
    required_voice_minutes INTEGER NOT NULL
        CHECK (required_voice_minutes > 0),
    override_reason TEXT,
    created_by_discord_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    UNIQUE (guild_id, attendance_date),
    CHECK (start_time < late_time AND late_time < close_time AND close_time < verification_end_time)
);

CREATE TABLE voice_presence_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    session_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    voice_channel_id TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    left_at TEXT,
    duration_seconds INTEGER,
    close_reason TEXT
        CHECK (
            close_reason IS NULL OR close_reason IN (
                'LEFT',
                'MOVED',
                'DISCONNECTED',
                'VERIFICATION_ENDED',
                'BOT_RECOVERY',
                'ADMIN'
            )
        ),
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

    CHECK (
        (left_at IS NULL AND duration_seconds IS NULL AND close_reason IS NULL)
        OR
        (left_at IS NOT NULL AND duration_seconds IS NOT NULL AND duration_seconds >= 0 AND close_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_voice_presence_open_unique
ON voice_presence_logs (session_id, member_id)
WHERE left_at IS NULL;

CREATE INDEX idx_voice_presence_session_member
ON voice_presence_logs (session_id, member_id, joined_at);

CREATE TABLE attendance_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_record_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('PENDING', 'VERIFIED', 'FAILED', 'WAIVED')),
    required_seconds INTEGER NOT NULL
        CHECK (required_seconds > 0),
    accumulated_seconds INTEGER NOT NULL
        DEFAULT 0
        CHECK (accumulated_seconds >= 0),
    verification_end_at TEXT NOT NULL,
    verified_at TEXT,
    failed_at TEXT,
    failure_reason TEXT
        CHECK (
            failure_reason IS NULL OR failure_reason IN (
                'NO_VOICE_JOIN',
                'INSUFFICIENT_DURATION',
                'ADMIN_REJECTED'
            )
        ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (attendance_record_id)
        REFERENCES attendance_records(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (session_id)
        REFERENCES attendance_sessions(id)
        ON DELETE RESTRICT,

    FOREIGN KEY (member_id)
        REFERENCES members(id)
        ON DELETE RESTRICT,

    UNIQUE (attendance_record_id),
    UNIQUE (session_id, member_id)
);

CREATE INDEX idx_attendance_verifications_status_end
ON attendance_verifications (status, verification_end_at);

CREATE INDEX idx_attendance_verifications_session_status
ON attendance_verifications (session_id, status);
