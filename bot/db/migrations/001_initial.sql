-- Discord 서버별 근태관리 설정
CREATE TABLE guild_settings (
    guild_id TEXT PRIMARY KEY,

    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',

    attendance_days TEXT NOT NULL
        DEFAULT 'MON,TUE,WED,THU,FRI',

    attendance_start TEXT NOT NULL
        DEFAULT '20:00',

    late_deadline TEXT NOT NULL
        DEFAULT '20:15',

    close_deadline TEXT NOT NULL
        DEFAULT '20:30',

    excuse_mode TEXT NOT NULL
        DEFAULT 'officer_approval'
        CHECK (
            excuse_mode IN (
                'auto',
                'officer_approval'
            )
        ),

    officer_role_id TEXT,

    attendance_channel_id TEXT,

    announcement_channel_id TEXT,

    weekly_report_enabled INTEGER NOT NULL
        DEFAULT 0
        CHECK (
            weekly_report_enabled IN (0, 1)
        ),

    created_at TEXT NOT NULL,

    updated_at TEXT NOT NULL,

    -- 출석 시작, 지각, 마감 시간은 반드시 순서대로 설정되어야 한다.
    CHECK (
        attendance_start < late_deadline
        AND late_deadline < close_deadline
    )
);


-- 출석 관리 대상자로 등록된 Discord 사용자
CREATE TABLE members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id TEXT NOT NULL,

    discord_id TEXT NOT NULL,

    display_name TEXT NOT NULL,

    is_active INTEGER NOT NULL
        DEFAULT 1
        CHECK (
            is_active IN (0, 1)
        ),

    activated_at TEXT NOT NULL,

    deactivated_at TEXT,

    created_by_discord_id TEXT NOT NULL,

    updated_at TEXT NOT NULL,

    FOREIGN KEY (guild_id)
        REFERENCES guild_settings(guild_id)
        ON DELETE RESTRICT,

    -- 한 서버에서 같은 Discord 사용자가 중복 등록되는 것을 막는다.
    UNIQUE (guild_id, discord_id)
);


-- 활성 대원 목록을 조회할 때 사용할 인덱스
CREATE INDEX idx_members_guild_active
ON members (
    guild_id,
    is_active
);