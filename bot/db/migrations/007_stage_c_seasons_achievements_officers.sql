CREATE TABLE seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED'
        CHECK (status IN ('SCHEDULED', 'ACTIVE', 'CLOSED', 'CANCELLED')),
    policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
    stats_dirty INTEGER NOT NULL DEFAULT 1 CHECK (stats_dirty IN (0, 1)),
    last_reconciled_at TEXT,
    created_by_discord_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    closed_at TEXT,
    cancelled_at TEXT,
    cancellation_reason TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    UNIQUE (guild_id, name),
    CHECK (length(trim(name)) > 0),
    CHECK (start_date <= end_date)
);

CREATE UNIQUE INDEX idx_seasons_one_active
ON seasons (guild_id)
WHERE status = 'ACTIVE';

CREATE INDEX idx_seasons_guild_status_dates
ON seasons (guild_id, status, start_date, end_date);

CREATE TABLE season_member_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    target_session_count INTEGER NOT NULL DEFAULT 0 CHECK (target_session_count >= 0),
    attendance_denominator INTEGER NOT NULL DEFAULT 0 CHECK (attendance_denominator >= 0),
    present_count INTEGER NOT NULL DEFAULT 0 CHECK (present_count >= 0),
    late_count INTEGER NOT NULL DEFAULT 0 CHECK (late_count >= 0),
    early_leave_count INTEGER NOT NULL DEFAULT 0 CHECK (early_leave_count >= 0),
    no_participation_count INTEGER NOT NULL DEFAULT 0 CHECK (no_participation_count >= 0),
    absent_count INTEGER NOT NULL DEFAULT 0 CHECK (absent_count >= 0),
    exempt_absent_count INTEGER NOT NULL DEFAULT 0 CHECK (exempt_absent_count >= 0),
    attendance_rate REAL NOT NULL DEFAULT 0 CHECK (attendance_rate >= 0 AND attendance_rate <= 100),
    on_time_rate REAL NOT NULL DEFAULT 0 CHECK (on_time_rate >= 0 AND on_time_rate <= 100),
    voice_seconds INTEGER NOT NULL DEFAULT 0 CHECK (voice_seconds >= 0),
    voice_verified_count INTEGER NOT NULL DEFAULT 0 CHECK (voice_verified_count >= 0),
    voice_failed_count INTEGER NOT NULL DEFAULT 0 CHECK (voice_failed_count >= 0),
    current_streak INTEGER NOT NULL DEFAULT 0 CHECK (current_streak >= 0),
    best_streak INTEGER NOT NULL DEFAULT 0 CHECK (best_streak >= 0),
    season_score INTEGER NOT NULL DEFAULT 0,
    final_personal_rank TEXT,
    officer_evaluation_score REAL NOT NULL DEFAULT 0,
    finalized_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (season_id) REFERENCES seasons (id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE,
    UNIQUE (season_id, member_id)
);

CREATE INDEX idx_season_member_stats_ranking
ON season_member_stats (season_id, season_score DESC, attendance_rate DESC, present_count DESC);

CREATE TABLE achievement_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    condition_type TEXT NOT NULL
        CHECK (condition_type IN (
            'FIRST_PRESENT',
            'ATTENDANCE_COUNT',
            'STREAK',
            'ON_TIME_COUNT',
            'VOICE_HOURS',
            'PERFECT_SEASON'
        )),
    threshold INTEGER NOT NULL DEFAULT 1 CHECK (threshold >= 0),
    reward_score INTEGER NOT NULL DEFAULT 0,
    title_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    once_per_season INTEGER NOT NULL DEFAULT 0 CHECK (once_per_season IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    UNIQUE (guild_id, code)
);

CREATE INDEX idx_achievement_definitions_guild_active
ON achievement_definitions (guild_id, is_active);

CREATE TABLE member_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    member_id INTEGER NOT NULL,
    achievement_definition_id INTEGER NOT NULL,
    season_id INTEGER,
    status TEXT NOT NULL DEFAULT 'EARNED' CHECK (status IN ('EARNED', 'REVOKED')),
    earned_at TEXT NOT NULL,
    score_event_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE,
    FOREIGN KEY (achievement_definition_id) REFERENCES achievement_definitions (id) ON DELETE CASCADE,
    FOREIGN KEY (season_id) REFERENCES seasons (id) ON DELETE CASCADE,
    FOREIGN KEY (score_event_id) REFERENCES score_events (id)
);

CREATE UNIQUE INDEX idx_member_achievements_global_once
ON member_achievements (member_id, achievement_definition_id)
WHERE season_id IS NULL;

CREATE UNIQUE INDEX idx_member_achievements_season_once
ON member_achievements (member_id, achievement_definition_id, season_id)
WHERE season_id IS NOT NULL;

CREATE TABLE title_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    title_name TEXT NOT NULL,
    source_achievement_definition_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (source_achievement_definition_id) REFERENCES achievement_definitions (id),
    UNIQUE (guild_id, title_name)
);

CREATE TABLE member_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    member_id INTEGER NOT NULL,
    title_definition_id INTEGER NOT NULL,
    is_equipped INTEGER NOT NULL DEFAULT 0 CHECK (is_equipped IN (0, 1)),
    unlocked_at TEXT NOT NULL,
    equipped_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE,
    FOREIGN KEY (title_definition_id) REFERENCES title_definitions (id) ON DELETE CASCADE,
    UNIQUE (member_id, title_definition_id)
);

CREATE UNIQUE INDEX idx_member_titles_one_equipped
ON member_titles (member_id)
WHERE is_equipped = 1;

CREATE TABLE achievement_role_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    achievement_definition_id INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (achievement_definition_id) REFERENCES achievement_definitions (id) ON DELETE CASCADE,
    UNIQUE (guild_id, achievement_definition_id)
);

CREATE TABLE officer_review_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    evaluation_window_days INTEGER NOT NULL DEFAULT 30 CHECK (evaluation_window_days >= 1),
    minimum_sessions INTEGER NOT NULL DEFAULT 5 CHECK (minimum_sessions >= 0),
    promotion_threshold REAL NOT NULL DEFAULT 80 CHECK (promotion_threshold >= 0 AND promotion_threshold <= 100),
    retention_threshold REAL NOT NULL DEFAULT 65 CHECK (retention_threshold >= 0 AND retention_threshold <= 100),
    replacement_score_gap REAL NOT NULL DEFAULT 10 CHECK (replacement_score_gap >= 0),
    officer_capacity INTEGER NOT NULL DEFAULT 5 CHECK (officer_capacity >= 0),
    promotion_cooldown_days INTEGER NOT NULL DEFAULT 14 CHECK (promotion_cooldown_days >= 0),
    demotion_cooldown_days INTEGER NOT NULL DEFAULT 14 CHECK (demotion_cooldown_days >= 0),
    member_role_id TEXT,
    officer_role_id TEXT,
    auto_review_enabled INTEGER NOT NULL DEFAULT 0 CHECK (auto_review_enabled IN (0, 1)),
    auto_apply_roles_enabled INTEGER NOT NULL DEFAULT 0 CHECK (auto_apply_roles_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE
);

CREATE TABLE officer_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    season_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PREVIEW'
        CHECK (status IN ('PREVIEW', 'COMPLETED', 'PARTIAL', 'FAILED', 'STALE', 'CANCELLED')),
    input_digest TEXT NOT NULL,
    created_by_discord_id TEXT,
    created_at TEXT NOT NULL,
    executed_by_discord_id TEXT,
    executed_at TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (season_id) REFERENCES seasons (id) ON DELETE SET NULL,
    UNIQUE (guild_id, input_digest)
);

CREATE TABLE officer_role_change_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    review_id INTEGER,
    member_id INTEGER,
    discord_id TEXT NOT NULL,
    action_type TEXT NOT NULL
        CHECK (action_type IN ('PROMOTE', 'DEMOTE', 'KEEP_OFFICER', 'KEEP_MEMBER')),
    from_role_id TEXT,
    to_role_id TEXT,
    status TEXT NOT NULL DEFAULT 'PLANNED'
        CHECK (status IN ('PLANNED', 'SUCCEEDED', 'FAILED', 'SKIPPED')),
    reason TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_settings (guild_id) ON DELETE CASCADE,
    FOREIGN KEY (review_id) REFERENCES officer_reviews (id) ON DELETE SET NULL,
    FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE SET NULL
);

CREATE INDEX idx_officer_role_change_logs_guild_created
ON officer_role_change_logs (guild_id, created_at DESC);
