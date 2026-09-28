-- 음성 검증 정책(요구 체류 시간, 검증 마감 시각, 감점)을 서버 설정으로 조정할 수 있게 한다.
-- 기존 값은 코드에 고정되어 있던 기본값과 같으므로 동작이 바뀌지 않는다.

ALTER TABLE guild_settings
ADD COLUMN voice_required_minutes INTEGER NOT NULL
    DEFAULT 60
    CHECK (voice_required_minutes > 0);

ALTER TABLE guild_settings
ADD COLUMN voice_verification_end_time TEXT NOT NULL
    DEFAULT '23:00';

ALTER TABLE guild_settings
ADD COLUMN voice_early_leave_penalty INTEGER NOT NULL
    DEFAULT -1
    CHECK (voice_early_leave_penalty <= 0);

ALTER TABLE guild_settings
ADD COLUMN voice_no_participation_penalty INTEGER NOT NULL
    DEFAULT -2
    CHECK (voice_no_participation_penalty <= 0);
