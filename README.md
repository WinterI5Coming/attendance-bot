# attendance-bot

Discord 슬래시 명령어 기반 출석 관리 봇. 서버별로 출석 시간을 설정하고,
매일 출석 세션을 자동으로 열고 닫으며, 출석 점수·연속 출석 보너스·
랭킹·사유 지각(결석) 승인까지 처리한다.

## 목차

- [주요 기능](#주요-기능)
- [아키텍처와 서비스 흐름](#아키텍처와-서비스-흐름)
- [명령어 목록](#명령어-목록)
- [설치 및 실행 방법](#설치-및-실행-방법)
- [데이터베이스와 마이그레이션](#데이터베이스와-마이그레이션)
- [프로젝트 구조](#프로젝트-구조)
- [테스트](#테스트)
- [실사 운영 검증 체크리스트](#실사-운영-검증-체크리스트)

## 주요 기능

- **서버 초기설정**: 간부 역할, 출석/공지 채널, 출석 요일, 출석 시간,
  사유 승인 방식을 서버별로 설정한다.
- **대원 관리**: Discord 사용자를 출석 대상 대원으로 등록/제외/조회한다.
- **출석 세션 자동 운영**: 설정된 시간에 맞춰 매일 세션을 자동 생성하고,
  마감 시간이 지나면 미체크 인원을 자동으로 결석 처리한다.
- **출석 체크인**: `/출석`으로 정상 출석·지각·사유 지각을 기록하고
  점수를 즉시 반영한다.
- **관리자 정정**: 간부가 이미 기록된 출석을 다른 상태로 정정하고,
  점수 차이를 자동 보정하며 감사 로그를 남긴다.
- **사유 지각/결석 신청**: 출석 시작 전에 사유를 신청하고, 간부가
  승인/거절하면 출석 기록과 점수에 자동으로 반영된다.
- **연속 출석 보너스**: 3회/7회 연속 출석 시 보너스 점수를 한 번만 지급한다.
- **랭킹/개인 리포트**: `/랭킹`으로 서버 전체 순위를, `/내정보`로 개인
  통계(출석률, 연속 출석, 최근 점수 변화)를 조회한다.
- **자동 공지**: 출석 시작/마감 시 지정한 채널에 안내 메시지를 자동 발송한다.

## 아키텍처와 서비스 흐름

### 계층 구조

```
Discord 슬래시 명령어
        │
        ▼
   bot/cogs/*        Discord 상호작용을 받아 입력을 검증하고 Service를 호출한다.
        │
        ▼
 bot/services/*      비즈니스 규칙(점수 계산, 상태 전이, 권한 판단)을 처리한다.
        │
        ▼
bot/repositories/*    SQLite에 직접 접근해 조회/삽입/갱신을 수행한다.
        │
        ▼
   bot/db/database.py  연결 관리와 SQL 마이그레이션 적용을 담당한다.
```

- **policies** (`bot/policies/`): 출석 상태별 점수(`score_policy.py`)와
  총점 구간별 계급(`rank_policy.py`) 같은 순수 규칙만 모아둔다.
- **utils** (`bot/utils/`): 시간대 변환, 출석 창 계산, 권한 검사처럼
  여러 Service에서 공통으로 쓰는 헬퍼를 둔다.
- **scheduler** (`bot/scheduler/attendance_loop.py`): 주기적으로 실행되는
  백그라운드 루프로, 세션 자동 생성/마감/공지를 트리거한다.

각 Service는 여러 Repository와 다른 Service(예: `StreakService`,
`ExcuseRepository`)를 생성자 주입으로 받아 조립되며, 실제 조립은
`bot/main.py`에서 한 번에 이뤄진다.

### 하루 출석 세션의 흐름

1. **세션 생성** — `AttendanceScheduler`가 매 tick마다 설정이 끝난 모든
   서버를 확인해 오늘 날짜 세션이 없으면 `guild_settings`의 출석
   시작/지각/마감 시간으로 세션을 생성한다(`SessionService.prepare_today_session`).
2. **시작 공지** — 세션이 `OPEN` 상태가 되면(`start_at` 도달) 공지 채널에
   시작 안내를 한 번만 보낸다(`start_announced_at` 기록으로 중복 방지).
3. **체크인** — 대원이 `/출석`을 실행하면 `AttendanceService.check_in`이
   - 세션이 열려 있는지, 이미 체크인했는지 확인하고
   - 현재 시각을 정상/지각 구간과 비교해 상태를 정하고
   - 승인된 사유 신청이 있으면 지각을 `EXCUSED_LATE`로 전환하고
   - 출석 기록 생성, 점수 이벤트 생성, 연속 출석 보너스 계산을
     하나의 DB 트랜잭션으로 원자적으로 처리한다.
4. **마감 처리** — 마감 시간이 지나면 스케줄러가
   `SessionService.process_overdue_sessions`를 호출해 아직 체크인하지
   않은 대원을 조회하고, 승인된 사유가 있으면 `EXCUSED_ABSENT`, 없으면
   `ABSENT`로 기록하며 각각의 점수 이벤트를 남긴 뒤 세션을 `CLOSED`로
   전환한다. 봇이 재시작되면 `recover_overdue_sessions`가 동일한 로직으로
   놓친 마감을 복구한다(중복 처리 방지).
5. **마감 공지** — 세션이 닫히면 마감 안내를 공지 채널에 한 번만 보낸다.
6. **조회/정정** — 이후 `/출석현황`으로 오늘 현황을, `/내정보`·`/랭킹`으로
   누적 통계를 확인할 수 있고, 간부는 `/출석수정`으로 기록을 정정하면
   점수 차액과 감사 로그(`audit_logs`)가 함께 남는다.

### 사유 지각/결석 신청 흐름

1. 대원이 출석 시작 전 `/사유신청`으로 날짜·사유·예상 시간을 제출하면
   `ExcuseService.create_request`가 날짜/요일/중복 여부를 검증하고
   `excuse_requests`에 `PENDING`(또는 서버 설정이 자동승인이면
   `AUTO_APPROVED`) 상태로 저장한다.
2. 간부가 `/사유목록`으로 대기 중인 신청을 확인하고 `/사유승인` 또는
   `/사유거절`을 실행한다.
3. 승인되면 `ExcuseService._reconcile_attendance_for_approval`이 실행되어
   - 이미 출석 기록이 있으면 `LATE → EXCUSED_LATE`, `ABSENT → EXCUSED_ABSENT`로
     상태를 바꾸고 점수 차액을 보정 이벤트로 기록하고,
   - 아직 체크인 전이면 이후 `/출석` 체크인이나 자동 마감 시점에
     반영된다.
4. 신청자는 아직 출석에 반영되지 않은 신청을 `/사유취소`로 취소할 수
   있다. 모든 승인/거절/취소는 `audit_logs`에 기록된다.

### 연속 출석과 랭킹

- `StreakService.calculate_current_streak`는 최근 세션부터 역순으로
  `PRESENT`/`LATE`/`EXCUSED_LATE`가 이어지는 횟수를 센다.
  `EXCUSED_ABSENT`는 연속 기록을 끊지 않지만 횟수도 올리지 않고,
  일반 `ABSENT`는 연속 기록을 끊는다.
- 정상 체크인마다 연속 횟수가 3 또는 7이 되면 `STREAK_BONUS` 점수를
  한 번만 지급한다(세션·대원·streak 값 기준 dedup key로 중복 방지).
- `/랭킹`은 활성 대원 전체의 총점 내림차순 → 연속 출석 내림차순 →
  이름순으로 정렬해 계급과 함께 보여준다.

### 자동 공지 스케줄러

`AttendanceScheduler`는 discord.py의 `tasks.loop`로 주기 실행되며, 매
tick마다 (1) 신규 세션 생성 → (2) 시작 공지 → (3) 마감 처리 →
(4) 마감 공지 순으로 실행한다. 공지 대상은 `announcement_channel_id`가
있으면 그 채널, 없으면 출석 채널로 보낸다.

## 명령어 목록

| 명령어 | 대상 | 설명 |
| --- | --- | --- |
| `/초기설정` | 서버 관리자 | 간부 역할, 채널, 출석 요일/시간, 사유 승인 방식을 최초 설정한다. |
| `/출석시간설정` | 서버 관리자 | 출석 시작/지각/마감 시간을 변경한다. |
| `/대원등록` | 간부 | Discord 사용자를 출석 대원으로 등록한다. |
| `/대원제외` | 간부 | 대원을 이후 출석 대상에서 제외한다. |
| `/대원목록` | 전체 | 현재 활성 대원 목록을 조회한다. |
| `/출석` | 전체 | 오늘 출석 세션에 체크인한다. |
| `/출석현황` | 전체 | 오늘 세션의 정상·지각·미체크 현황을 조회한다. |
| `/출석수정` | 간부 | 특정 날짜의 출석 기록을 정정한다. |
| `/사유신청` | 전체 | 출석 시작 전 사유 지각/결석을 신청한다. |
| `/사유취소` | 전체 | 아직 반영되지 않은 내 신청을 취소한다. |
| `/사유목록` | 전체(본인)/간부(전체조회) | 사유 신청 목록을 조회한다. |
| `/사유승인` | 간부 | 대기 중인 사유 신청을 승인한다. |
| `/사유거절` | 간부 | 대기 중인 사유 신청을 거절한다. |
| `/내정보` | 전체 | 내 출석 통계, 연속 출석, 최근 점수 변화를 조회한다. |
| `/랭킹` | 전체 | 서버 출석 점수 랭킹을 조회한다. |
| `/핑` | 전체 | 봇 연결 상태와 응답 속도를 확인한다. |

"간부"는 `/초기설정`에서 지정한 역할 보유자 또는 서버 소유자/관리자를
말한다(`bot/utils/permissions.py`).

## 설치 및 실행 방법

### 1. 사전 준비

- Python 3.11 이상 (개발/검증은 3.13 기준)
- Discord 개발자 포털(https://discord.com/developers/applications)에서
  애플리케이션과 봇을 생성하고 **Bot Token**을 발급받는다.
- 봇 초대 시 OAuth2 URL Generator에서 `bot`, `applications.commands`
  스코프와 최소한 `Send Messages`, `Embed Links`, `Read Message History`
  권한을 선택해 서버에 초대한다. (봇은 메시지 콘텐츠 인텐트가 필요 없다.)
- 슬래시 명령어를 테스트할 서버(길드)의 ID를 확인해둔다
  (서버 아이콘 우클릭 → ID 복사, 개발자 모드 필요).

### 2. 프로젝트 설정

```powershell
git clone <repository-url>
cd attendance-bot

python -m venv venv
venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. 환경변수 설정

`.env.example`을 복사해 `.env`를 만들고 값을 채운다.

```powershell
Copy-Item .env.example .env
```

`.env` 항목:

| 변수 | 필수 | 설명 |
| --- | --- | --- |
| `DISCORD_TOKEN` | 예 | Discord 봇 토큰 |
| `DEVELOPMENT_GUILD_ID` | 예 | 슬래시 명령어를 즉시 동기화할 서버 ID |
| `DB_PATH` | 아니오 | SQLite 파일 경로 (기본값 `data/attendance.db`) |
| `TIMEZONE` | 아니오 | 기본 타임존 (기본값 `Asia/Seoul`) |
| `LOG_LEVEL` | 아니오 | 로그 레벨 (기본값 `INFO`) |
| `DEFAULT_ATTENDANCE_DAYS` | 아니오 | `/초기설정` 기본 출석 요일 |
| `DEFAULT_ATTENDANCE_START` | 아니오 | `/초기설정` 기본 출석 시작 시간 (`HH:MM`) |
| `DEFAULT_LATE_DEADLINE` | 아니오 | `/초기설정` 기본 지각 기준 시간 |
| `DEFAULT_CLOSE_DEADLINE` | 아니오 | `/초기설정` 기본 마감 시간 |
| `DEFAULT_EXCUSE_MODE` | 아니오 | `auto` 또는 `officer_approval` |

`DISCORD_TOKEN`, `DEVELOPMENT_GUILD_ID`가 없으면 `bot/config.py`에서
바로 실행이 중단된다.

> 슬래시 명령어는 현재 `DEVELOPMENT_GUILD_ID`로 지정한 서버에만
> 동기화된다(`bot/main.py`의 `tree.copy_global_to` / `tree.sync`).
> 다른 서버에서도 쓰려면 해당 서버 ID로 값을 바꾸거나, 글로벌
> 동기화 방식으로 코드를 확장해야 한다.

### 4. 봇 실행

```powershell
venv\Scripts\python.exe -m bot.main
```

실행하면 다음이 자동으로 이뤄진다.

1. `data/` 폴더와 SQLite 파일이 없으면 생성한다.
2. `bot/db/migrations/*.sql`을 버전 순서대로 적용한다
   (이미 적용된 마이그레이션은 건너뛴다).
3. 모든 Cog를 등록하고, 개발 서버에 슬래시 명령어를 동기화한다.
4. 재시작 복구(`recover_overdue_sessions`)를 한 번 실행해 다운타임 동안
   놓친 마감 처리를 따라잡는다.
5. `AttendanceScheduler`를 시작해 이후 주기적으로 세션 생성/공지/마감을
   반복한다.

정상 실행되면 `/초기설정`으로 서버를 설정한 뒤 `/대원등록`으로 대원을
등록하고 나머지 명령어를 사용할 수 있다.

## 데이터베이스와 마이그레이션

- SQLite 파일 하나로 동작하며, WAL 모드와 `busy_timeout`을 사용해
  동시 접근을 처리한다(`bot/db/database.py`).
- 마이그레이션은 `bot/db/migrations/`에 번호 순서(`001_`, `002_`, ...)로
  둔 `.sql` 파일이며, `schema_migrations` 테이블로 적용 여부를 추적한다.
  새 마이그레이션은 다음 번호로 파일만 추가하면 다음 실행 시 자동 적용된다.
- 주요 테이블: `guild_settings`, `members`, `attendance_sessions`,
  `attendance_session_members`, `attendance_records`, `score_events`,
  `excuse_requests`, `audit_logs`.

## 프로젝트 구조

```
bot/
  cogs/          Discord 슬래시 명령어 (입력 검증, 응답 메시지 구성)
  services/      비즈니스 로직 (상태 전이, 점수/연속출석 계산, 트랜잭션 조립)
  repositories/  SQLite 접근 계층 (테이블별 CRUD)
  policies/      점수/계급 등 순수 규칙 상수와 함수
  scheduler/     출석 세션 자동 생성/마감/공지 백그라운드 루프
  utils/         시간대 변환, 권한 검사 등 공통 유틸리티
  db/            연결 관리자와 SQL 마이그레이션 파일
  config.py      환경변수 로딩과 검증
  main.py        전체 조립과 봇 실행 진입점
tests/
  integration/   실제 SQLite에 대해 마이그레이션과 서비스 흐름을 검증
```

## 테스트

```powershell
venv\Scripts\python.exe -m pytest
```

## 실사 운영 검증 체크리스트

자동 테스트로 검증하기 어려운 Discord 권한, 채널 공지, 실제 시간
경과에 따른 동작은 실제 서버에서 아래 절차로 직접 확인한다.

### Phase 1: 기본 출석/마감/정정

#### 세션 1: 일반 마감 흐름

- `/초기설정`이 완료되어 있는지 확인한다.
- `/대원목록`에서 활성 대원이 의도한 인원인지 확인한다.
- 출석 시간에 일부 사용자는 `/출석`으로 정상 출석 또는 지각 처리한다.
- 일부 사용자는 미체크 상태로 둔다.
- 마감 후 `attendance_records`에 미체크 사용자의 `ABSENT`가 생성됐는지 확인한다.
- `score_events`에 결석 점수 `-3`이 생성됐는지 확인한다.
- `attendance_sessions.status`가 `CLOSED`인지 확인한다.

#### 세션 2: 재시작 복구 흐름

- 세션이 `OPEN`인 상태에서 봇을 종료한다.
- 마감 시각이 지난 뒤 봇을 다시 실행한다.
- 재시작 복구 로그가 실행됐는지 확인한다.
- 미체크 사용자만 `ABSENT` 처리됐는지 확인한다.
- 같은 복구를 다시 실행해도 중복 결석과 중복 점수가 생기지 않는지 확인한다.

#### 세션 3: 관리자 정정 흐름

- 마감 후 `/내정보`로 총점, 출석률, 최근 점수 변화를 확인한다.
- `/출석수정`으로 `ABSENT`를 `PRESENT`로 변경한다.
- `score_events`에 정정 점수 `+6`이 추가됐는지 확인한다.
- `audit_logs`에 `ATTENDANCE_CORRECTED` 기록이 생성됐는지 확인한다.
- `/내정보` 통계와 최근 점수 변화가 정정 결과를 반영하는지 확인한다.

확인 대상 테이블: `attendance_sessions`, `attendance_session_members`,
`attendance_records`, `score_events`, `audit_logs`.

### Phase 2: 사유 신청, 연속 출석, 랭킹, 자동 공지

#### 세션 1: 사유 지각 흐름

- 출석 시작 전 `/사유신청`으로 오늘 날짜 사유를 신청한다.
- 간부가 `/사유목록 전체조회:True 상태:대기`로 신청을 확인한다.
- 간부가 `/사유승인`으로 신청을 승인한다.
- 사용자가 지각 시간대에 `/출석`을 실행한다.
- `attendance_records.status`가 `EXCUSED_LATE`인지 확인한다.
- `score_events`의 해당 출석 점수가 `0`인지 확인한다.
- `/내정보`와 `/출석현황`에 사유 지각이 반영되는지 확인한다.

#### 세션 2: 사유 결석 흐름

- 출석 시작 전 `/사유신청`으로 오늘 날짜 사유를 신청한다.
- 간부가 `/사유승인`으로 승인한다.
- 사용자는 `/출석`을 실행하지 않는다.
- 마감 후 `attendance_records.status`가 `EXCUSED_ABSENT`인지 확인한다.
- `score_events`의 해당 결석 점수가 `-1`인지 확인한다.

#### 세션 3: 취소와 거절 흐름

- 대기 중인 신청을 `/사유취소`로 취소할 수 있는지 확인한다.
- 이미 출석 기록에 반영된 신청은 취소되지 않는지 확인한다.
- 간부가 `/사유거절`을 실행하면 상태가 `REJECTED`로 바뀌는지 확인한다.
- 일반 사용자가 전체 목록, 승인, 거절 명령을 사용할 수 없는지 확인한다.

#### 세션 4: 연속 출석과 랭킹

- 같은 대원이 3회 연속 출석하면 `STREAK_BONUS` `+2`가 한 번만 생성되는지 확인한다.
- 7회 연속 출석하면 `STREAK_BONUS` `+5`가 한 번만 생성되는지 확인한다.
- 사유 결석은 연속 출석을 끊지 않지만 횟수는 올리지 않는지 확인한다.
- 일반 결석은 연속 출석을 끊는지 확인한다.
- `/랭킹`이 총점 내림차순, 연속 출석 내림차순, 이름순으로 표시되는지 확인한다.

#### 세션 5: 자동 공지

- 출석 시작 시 announcement 채널에 시작 공지가 한 번만 올라오는지 확인한다.
- 출석 마감 시 마감 공지가 한 번만 올라오는지 확인한다.
- `attendance_sessions.start_announced_at`과 `close_announced_at`이 기록되는지 확인한다.
