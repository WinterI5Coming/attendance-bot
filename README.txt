AttendanceBot 실행 안내
======================

최초 실행 방법
--------------
1. .env.example 파일을 복사합니다.
2. 복사한 파일 이름을 .env로 변경합니다.
3. .env 파일에 Discord Bot Token을 입력합니다.
   예: DISCORD_BOT_TOKEN=your_discord_bot_token
4. DEVELOPMENT_GUILD_ID에 명령어를 동기화할 Discord 서버 ID를 입력합니다.
5. AttendanceBot.exe를 더블클릭합니다.
6. Discord에서 봇이 온라인 상태인지 확인합니다.

종료 방법
---------
- 실행 중인 콘솔 창에서 Ctrl+C를 누릅니다.
- 가능하면 콘솔 창을 강제로 닫는 것보다 Ctrl+C를 권장합니다.

데이터 백업하기
---------------
1. 가능하면 실행 중인 Discord 봇을 종료합니다.
2. AttendanceBotManager.exe를 실행합니다.
3. 데이터 백업하기 버튼을 누릅니다.
4. 백업 성공 메시지를 확인합니다.
5. 백업 폴더 열기 버튼을 누릅니다.
6. 생성된 .db 파일과 같은 이름의 .json 파일을 USB, 클라우드 저장소 또는 메신저 등을 통해 새 컴퓨터로 옮깁니다.

새 컴퓨터에서 복원하기
---------------------
1. 새 컴퓨터에서 AttendanceBot.exe가 실행 중이지 않은지 확인합니다.
2. AttendanceBotManager.exe를 실행합니다.
3. 백업 파일 복원하기 버튼을 누릅니다.
4. 옮겨온 .db 파일을 선택합니다.
5. 검증 및 복원 결과를 확인합니다.
6. 복원이 완료되면 AttendanceBot.exe를 실행합니다.

명령행 사용법
------------
- AttendanceBotManager.exe --backup
- AttendanceBotManager.exe --restore "C:\Backup\attendance_backup_20260712_193520.db"
- AttendanceBotManager.exe --validate "C:\Backup\attendance_backup_20260712_193520.db"
- AttendanceBotManager.exe --list-backups
- AttendanceBotManager.exe --reset-all-data

전체 데이터 초기화
----------------
새 사유 신청 정책을 적용하면서 기존 운영 데이터를 모두 비워야 할 때만 사용합니다.

1. 실행 중인 AttendanceBot.exe를 종료합니다.
2. 명령 프롬프트 또는 PowerShell에서 AttendanceBotManager.exe가 있는 폴더로 이동합니다.
3. AttendanceBotManager.exe --reset-all-data 를 실행합니다.
4. 경고 내용을 확인한 뒤 정확히 RESET ALL DATA 를 입력합니다.
5. 초기화 전에 backups 폴더에 before_policy_reset_날짜_시간.db 백업과 같은 이름의 .json 메타데이터가 자동 생성됩니다.
6. 초기화 후 AttendanceBot.exe를 다시 실행하고 Discord에서 /초기설정 및 /대원등록을 다시 진행합니다.

주의: 이 명령은 출석 기록, 대원, 점수, 서버 설정, 사유 신청 데이터를 삭제합니다. 문구를 다르게 입력하면 초기화는 취소됩니다.

사유 신청 정책
------------
- 기본 마감: 출석일 1일 전 23:00, Asia/Seoul 기준
- 일반 사용자는 마감 이후 /사유신청을 할 수 없습니다.
- 사유 유형은 결석, 지각, 조퇴 중 하나를 선택합니다.
- 사유 신청은 관리자 또는 간부가 승인해야 출석 판정에 반영됩니다.
- 긴급 예외는 관리자 또는 간부가 /사유예외등록으로 등록합니다.
- 정책 확인은 /사유정책조회, 공개 안내는 /사유정책공지, 마감 시간 변경은 /사유정책설정을 사용합니다.

파일 설명
---------
- AttendanceBot.exe: Discord 출석 봇 실행 파일
- AttendanceBotManager.exe: 데이터베이스 백업 및 복원 프로그램
- .env: Discord Bot Token 설정 파일
- data/attendance.db: 출석 기록과 설정 데이터베이스
- backups/: 백업 파일과 메타데이터 저장 폴더
- logs/attendance-bot.log: 봇 실행 및 오류 로그
- logs/database-manager.log: 백업 및 복원 작업 로그

주의 사항
---------
- 두 컴퓨터에서 같은 Discord 봇을 동시에 실행하지 마세요.
- 봇 실행 중에는 컴퓨터가 켜져 있어야 합니다.
- 인터넷 연결이 필요합니다.
- 봇을 실행한 컴퓨터가 절전 모드에 들어가면 연결이 중단될 수 있습니다.
- .env 파일은 다른 사람에게 공유하면 안 됩니다.
- data 폴더를 삭제하면 기존 출석 기록이 사라질 수 있습니다.
- 복원 전에 현재 데이터가 자동으로 백업됩니다.
- 오래된 백업을 복원하면 해당 백업 시점 이후의 데이터는 현재 DB에서 보이지 않게 됩니다.
- .db 파일의 이름을 임의로 바꾸는 것은 가능하지만 같은 이름의 .json 메타데이터와 함께 관리하는 것을 권장합니다.
- 백업 파일을 수정하거나 다른 SQLite DB로 교체하지 마세요.
- Discord 봇이 실행 중인 상태에서는 복원하지 마세요.

오류 발생 시
------------
- logs/attendance-bot.log 또는 logs/database-manager.log 파일을 확인합니다.
- .env에 토큰이 올바르게 입력되었는지 확인합니다.
- Discord Developer Portal에서 봇 권한과 Intent 설정을 확인합니다.
- 동일한 봇 프로그램이 이미 실행 중인지 확인합니다.
