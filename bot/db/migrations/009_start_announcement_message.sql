-- 출석 시작 공지 메시지 ID를 저장해 마감 시 [출석하기] 버튼을 비활성화할 수 있게 한다.
ALTER TABLE attendance_sessions
ADD COLUMN start_announcement_message_id TEXT;
