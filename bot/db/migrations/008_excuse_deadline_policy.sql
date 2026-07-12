-- Excuse request deadline policy and richer excuse metadata.

ALTER TABLE guild_settings
ADD COLUMN excuse_deadline_time TEXT NOT NULL
    DEFAULT '23:00';

ALTER TABLE guild_settings
ADD COLUMN excuse_deadline_days_before INTEGER NOT NULL
    DEFAULT 1
    CHECK (excuse_deadline_days_before >= 0);

ALTER TABLE guild_settings
ADD COLUMN require_excuse_approval INTEGER NOT NULL
    DEFAULT 1
    CHECK (require_excuse_approval IN (0, 1));

ALTER TABLE guild_settings
ADD COLUMN allow_late_excuse INTEGER NOT NULL
    DEFAULT 0
    CHECK (allow_late_excuse IN (0, 1));

ALTER TABLE excuse_requests
ADD COLUMN attendance_session_id INTEGER;

ALTER TABLE excuse_requests
ADD COLUMN excuse_type TEXT NOT NULL
    DEFAULT 'ABSENCE'
    CHECK (excuse_type IN ('ABSENCE', 'LATE', 'EARLY_LEAVE'));

ALTER TABLE excuse_requests
ADD COLUMN deadline_at TEXT;

ALTER TABLE excuse_requests
ADD COLUMN processed_at TEXT;

ALTER TABLE excuse_requests
ADD COLUMN processed_by TEXT;

ALTER TABLE excuse_requests
ADD COLUMN admin_note TEXT;

ALTER TABLE excuse_requests
ADD COLUMN is_admin_override INTEGER NOT NULL
    DEFAULT 0
    CHECK (is_admin_override IN (0, 1));

ALTER TABLE excuse_requests
ADD COLUMN approval_type TEXT NOT NULL
    DEFAULT 'STANDARD'
    CHECK (approval_type IN ('STANDARD', 'ADMIN_OVERRIDE'));

ALTER TABLE excuse_requests
ADD COLUMN updated_at TEXT;

CREATE INDEX idx_excuse_requests_session
ON excuse_requests (attendance_session_id);

CREATE INDEX idx_excuse_requests_type_status
ON excuse_requests (excuse_type, status);
