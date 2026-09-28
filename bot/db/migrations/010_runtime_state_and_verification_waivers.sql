-- 운영 안정성 보강: 스케줄러 하트비트, 음성 검증 면제 사유, 봇 제거 서버 표시.

-- 스케줄러가 마지막으로 살아 있던 시각 등 프로세스 재시작 후 참고할 값을 저장한다.
CREATE TABLE runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 세션 취소, 출석 정정, 조퇴 사유 승인 등으로 검증이 면제된 이유를 남긴다.
ALTER TABLE attendance_verifications
ADD COLUMN waived_reason TEXT;

-- 봇이 서버에서 제거된 시각. NULL이 아니면 스케줄러가 해당 서버를 건너뛴다.
ALTER TABLE guild_settings
ADD COLUMN bot_removed_at TEXT;
