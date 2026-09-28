# Discord Attendance Bot

> Discord 서버의 출석, 사유 신청, 점수, 리포트를 SQLite 기반으로 관리하는 근태관리봇입니다.  
> A SQLite-backed Discord attendance bot for check-ins, excuses, scores, and reports.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Discord.py](https://img.shields.io/badge/discord.py-slash%20commands-5865F2)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57)
![Tests](https://img.shields.io/badge/tests-156%20passed-brightgreen)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF)

## Table Of Contents

- [Overview](#overview)
- [Features](#features)
- [Command Guide](#command-guide)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Database And Migrations](#database-and-migrations)
- [Testing](#testing)
- [Operations Checklist](#operations-checklist)
- [English Summary](#english-summary)

## Overview

이 프로젝트는 Discord 커뮤니티의 반복적인 근태 운영을 자동화합니다. 대원을 등록하고, 매일 정해진 시간에 출석 세션을 열고, 출석/지각/결석/사유 처리를 점수 장부와 함께 관리합니다.

The bot is designed for communities that need repeatable attendance operations: daily check-in sessions, officer approvals, attendance corrections, score ledgers, and rankings.

핵심 원칙:

- 기존 점수 기록은 삭제하거나 수정하지 않고 `score_events`에 보정 이벤트를 추가합니다.
- 마이그레이션은 버전 순서대로 추가하며 기존 migration 파일을 수정하지 않습니다.
- 봇은 Discord 역할을 부여하거나 변경하지 않습니다. 간부 역할은 권한 확인에만 사용합니다.
- 봇이 꺼져 있던 기간의 출석일은 결석 처리하지 않고 "자동 취소" 세션으로만 남깁니다.

## Features

- 서버별 초기 설정: 간부 역할, 출석 채널, 공지 채널, 출석 요일과 시간
- 대원 관리: 등록, 제외, 활성 대원 목록 조회
- 출석 세션 자동 운영: 세션 생성, 시작 공지, 마감 처리, 재시작 복구
- 출석 체크: 출석 공지의 **[출석하기] 버튼** 한 번으로 체크인 (정상/지각/사유 판정, 마감 시 버튼 잠금)
- 사유 신청: **입력창(모달)** 으로 신청 → 간부에게 접수 알림 → **[검토하기] 버튼**으로 승인/거절
- 점수 장부: 출석 점수, 보정 점수, 평가 점수, 수동 조정, 취소 보정
- 리포트: 내 정보, 공개 리포트, 랭킹, 주간 보고
- 음성 검증(선택): 체크인 후 지정 음성 채널에 일정 시간 머물러야 검증 완료, 미달 시 감점. 요구 시간, 마감 시각, 감점은 `/설정 음성검증`으로 조정
- 다운타임 복구: 재시작 시 놓친 마감 처리, 음성 채널 재실 상태 재조정, 열리지 못한 출석일 표시
- 서버 변동 반영: 서버를 떠난 대원은 자동 제외, 봇이 제거된 서버는 자동 작업 대상에서 제외
- 상세 도움말: Discord 안에서 `/도움말`로 명령 사용법 확인

## Command Guide

명령은 기능별 그룹(`/그룹 하위명령`)으로 묶여 있으며, 봇 안에서 가장 자세한 사용법은 `/도움말` 명령으로 확인할 수 있습니다.

```text
/도움말
/도움말 카테고리:설정 | 대원 | 출석 | 사유 | 리포트 | 점수
```

| Group | Commands | Permission |
| --- | --- | --- |
| `/설정` | `초기화`(관리자), `출석시간`(관리자), `조회`, `변경`, `음성검증` | Officer/admin |
| `/대원` | `등록`, `제외` | Officer/admin · `목록` Everyone |
| 공지 버튼 | 출석 공지의 **[✅ 출석하기]**, 접수 알림의 **[🗂️ 검토하기]** | Member / Officer |
| `/출석` | `체크인`(버튼과 동일) Member · `현황` Everyone · `수정`, `오늘취소`, `오늘재개`, `검증현황` Officer/admin | 명령별 상이 |
| `/사유` | `신청`(입력창), `취소`, `목록`, `정책` | Member · `검토`(선택+승인/거절 버튼), `예외등록`, `정책`(변경/공지) Officer/admin |
| `/점수` | `평가`, `평가취소`, `조정` | Officer/admin |
| `/내정보 [사용자]` | 내 통계(비공개) 또는 지정 사용자의 공개 리포트 | Everyone |
| `/랭킹`, `/주간보고` | 서버 랭킹, 주간 통계 | Everyone |
| `/도움말`, `/핑` | 도움말, 상태 확인 | Everyone |

## Architecture

```text
main.py             실행 진입점 (로깅 설정 후 봇 실행)
        |
        v
bot/app/*           클라이언트 조립: 컨테이너(DI), 이벤트, 시스템 명령
        |
        v
Discord slash commands
        |
        v
bot/cogs/*          사용자 입력 검증, 권한 확인(cogs/common.py), 응답 메시지 구성
        |
        v
bot/services/*      비즈니스 규칙, 트랜잭션 흐름, 점수/통계 계산
        |
        v
bot/repositories/*  SQLite 쿼리, CRUD, 조회 전용 집계
        |
        v
bot/db/database.py  연결 관리, PRAGMA, SQL migration 적용
```

주요 디렉터리:

| Path | Description |
| --- | --- |
| `bot/app/` | Bot client, dependency container, lifecycle events, system commands |
| `bot/cogs/` | Discord slash command handlers (`common.py` holds shared guards) |
| `bot/ui/views/` | Persistent buttons, modals, select menus for check-in and excuse review |
| `bot/services/` | Business rules and orchestration |
| `bot/repositories/` | SQLite data access layer |
| `bot/policies/` | Score and rank policies |
| `bot/scheduler/` | Attendance and backup background loops |
| `bot/ui/` | Embed factory, message theme, value formatters |
| `bot/runtime/` | Paths, logging, time provider |
| `bot/db/migrations/` | Versioned SQLite migrations |
| `tests/` | Unit and integration tests (`helpers.py` holds shared test helpers) |

## Quick Start

### Docker로 상시 운영 (권장)

```bash
git clone <repository-url>
cd attendance-bot
cp .env.example .env        # DISCORD_BOT_TOKEN 입력
docker compose up -d --build
docker compose logs -f bot  # "Synced N global slash commands." 확인
```

- SQLite DB와 백업은 호스트의 `./data/`, 로그는 `./logs/`에 남습니다. 컨테이너를 지워도 데이터는 유지됩니다.
- 재시작 정책이 `unless-stopped`라 서버 재부팅 시 자동으로 다시 뜹니다.
- `docker compose stop`은 SIGTERM으로 정상 종료를 요청하고(유예 30초), 봇은 스케줄러를 멈추고 종료 백업을 남긴 뒤 연결을 닫습니다.
- HEALTHCHECK: 스케줄러가 매분 갱신하는 `data/heartbeat` 파일이 3분 이상 멈추면 컨테이너가 `unhealthy`로 표시됩니다 (`docker compose ps`로 확인).
- 업데이트: `git pull && docker compose up -d --build`

### 로컬 개발 실행

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env   # DISCORD_BOT_TOKEN, DEVELOPMENT_GUILD_ID 입력
python main.py
```

`DEVELOPMENT_GUILD_ID`를 설정하면 그 서버에만 즉시 동기화되어 명령 변경을 바로 확인할 수 있습니다. 비우면 글로벌 동기화(반영까지 최대 1시간)입니다.

처음 실행하면 다음 작업이 자동으로 진행됩니다.

1. `DATA_DIR`(기본 `data/`)에 SQLite DB 파일을 준비합니다.
2. `bot/db/migrations/*.sql`을 버전 순서대로 적용합니다.
3. Cog를 등록하고 slash command를 동기화합니다.
4. 누락된 출석 마감 처리를 복구합니다.
5. 출석/백업 스케줄러를 시작합니다.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Yes | - | Discord Bot Token (`DISCORD_TOKEN` also accepted) |
| `DEVELOPMENT_GUILD_ID` | No | - | Set for instant guild-only sync while developing; empty = global sync |
| `DATA_DIR` | No | `data/` | Directory for SQLite DB and `backups/` |
| `TIMEZONE` | No | `Asia/Seoul` | Default guild timezone |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `DEFAULT_ATTENDANCE_DAYS` | No | `MON,TUE,WED,THU,FRI` | Default attendance weekdays |
| `DEFAULT_ATTENDANCE_START` | No | `21:30` | Default check-in open time |
| `DEFAULT_LATE_DEADLINE` | No | `21:40` | Default late threshold |
| `DEFAULT_CLOSE_DEADLINE` | No | `21:45` | Default close time |
| `DEFAULT_EXCUSE_MODE` | No | `officer_approval` | `auto` or `officer_approval` |
| `EXCUSE_DEADLINE_TIME` | No | `23:00` | Default excuse request cutoff time in guild timezone |
| `EXCUSE_DEADLINE_DAYS_BEFORE` | No | `1` | Cutoff date offset before the attendance date |
| `REQUIRE_EXCUSE_APPROVAL` | No | `true` | New excuse requests require officer/admin approval |
| `ALLOW_LATE_EXCUSE` | No | `false` | Registered members cannot submit after the cutoff |

## Voice Verification

체크인만으로는 실제 참여를 보장하지 못하므로, 켜 두면 체크인 이후 지정한 음성 채널(또는 카테고리)에 머문 시간을 누적해 검증합니다.

- 켜기: `/설정 음성검증 사용:True 채널:#훈련음성` (카테고리로 지정하면 그 안의 모든 음성 채널 인정)
- 규칙: 체크인 후 **60분** 이상 체류 시 검증 성공, 검증 마감은 **23:00**(서버 시간대). 체크인 전 체류 시간은 인정하지 않습니다.
- 조정: `/설정 음성검증 ... 요구시간:30 검증마감:22:30 미참여감점:-3 시간부족감점:0`. 검증 마감은 최소 "출석 마감 + 요구 시간"까지 자동으로 늦춰지므로 마감 직전 체크인도 요구 시간을 채울 수 있습니다. 새 규칙은 다음에 만들어지는 세션부터 적용됩니다.
- 결과: 마감 시 미참여는 `NO_PARTICIPATION_PENALTY`(기본 -2), 시간 부족은 `EARLY_LEAVE_PENALTY`(기본 -1) 점수 이벤트가 추가되며 출석 기록 자체는 바뀌지 않습니다. 감점을 0으로 두면 이벤트를 만들지 않습니다.
- 면제: 세션 취소, `/출석 수정`으로 결석 정정, 조퇴(`EARLY_LEAVE`) 사유 승인, 당일 검증 끄기 시 검증은 `WAIVED`로 표시되고 이미 부과된 감점은 반대 이벤트로 되돌립니다.
- 재시작: 봇이 다시 뜨면 실제 음성 채널 재실 상태와 열린 로그를 맞춥니다. 다운타임 중 나간 대원은 마지막 스케줄러 하트비트 시각까지만, 들어온 대원은 재시작 시각부터 인정합니다.
- 확인: `/출석 검증현황`으로 오늘 대원별 대기/성공/실패와 누적 시간을 볼 수 있습니다.
- 전제: Developer Portal에서 봇의 **Server Members Intent는 불필요**하지만, 봇이 음성 상태 이벤트를 받도록 `voice_states` intent(코드에서 활성화)가 허용되어야 하고, 대상 채널을 볼 수 있는 권한이 있어야 합니다.

첫 도입 시 검증 절차: 음성 검증을 켜고 → 출석 공지 버튼으로 체크인 → 대상 음성 채널에 입장/퇴장 → `/출석 검증현황`에서 누적 시간이 늘어나는지 확인 → 23:00 이후 `/내정보`의 최근 점수 변동에서 검증 결과를 확인합니다.

## Excuse Deadline Policy

기본 사유 신청 정책은 `Asia/Seoul` 기준 출석일 전날 23:00까지 신청, 관리자 승인 필수, 마감 이후 일반 사용자 신청 불가입니다.

- 사용자는 `/사유 신청`에서 `결석`, `지각`, `조퇴` 유형을 선택해 신청합니다.
- 신청은 `PENDING` 상태로 생성되고 `/사유 검토`(또는 접수 알림의 [검토하기] 버튼)에서 승인한 뒤 출석 판정과 점수에 반영됩니다.
- `조퇴` 사유가 승인되면 그날의 음성 검증은 면제됩니다.
- 마감 이후 긴급 예외는 관리자/간부가 `/사유 예외등록`으로 등록합니다.
- 정책 확인은 `/사유 정책`, 마감 시간 변경은 `/사유 정책 마감시간:23:00 마감일수:1`, 공개 공지는 `/사유 정책 공지:True`를 사용합니다.
- 환경 기본값은 새 서버 초기 설정에 적용되며, 이미 생성된 서버는 `/사유 정책`으로 변경합니다.

## Message Design

봇 응답은 `bot/ui/` 계층을 기준으로 통일합니다.

- `bot/ui/message_theme.py`: 성공, 정보, 경고, 오류, 관리자 메시지 색상
- `bot/ui/embed_factory.py`: 표준 Embed 생성 규칙
- `bot/ui/formatters.py`: 점수, 출석 상태, 검증 상태, 날짜/시간 표시
- `bot/ui/*_messages.py`: 서비스 결과 → 메시지/Embed 변환 (Cog와 버튼이 공유)
- `bot/ui/views/`: 영속 버튼(`CheckInView`, `ExcuseNoticeView`), 사유 신청 모달, 검토 선택 메뉴

일일 흐름:

1. 출석 시작 시각에 스케줄러가 공지 채널에 Embed + **[출석하기]** 버튼을 올립니다. 대원은 버튼만 누르면 됩니다.
2. 마감되면 마감 공지를 올리고 시작 공지의 버튼을 **[출석 마감]** 으로 잠급니다.
3. `/사유 신청`은 유형(선택)·날짜·사유 입력창을 띄우고, 접수되면 공지 채널에 **[검토하기]** 버튼이 달린 알림이 올라갑니다(사유 본문은 비공개).
4. 간부가 버튼 또는 `/사유 검토`를 누르면 대기 목록 → 선택 → 상세 확인 → [승인]/[거절](거절 사유 입력창) 순서로 처리합니다.

버튼은 `timeout=None` 영속 뷰로 등록되어 봇을 재시작해도 과거 공지의 버튼이 계속 동작합니다. 검토 화면은 15분 뒤 만료되며 만료 시 버튼이 잠기고 안내가 표시됩니다.

공지 채널이 삭제되었거나 봇에 전송 권한이 없으면 출석 채널로 대체해 보내고, 그마저 불가능하면 공지를 생략으로 기록하고 로그에 `ERROR`로 남깁니다(매분 재시도하지 않음). 대원은 `/출석 체크인`으로 출석할 수 있습니다.

공개 메시지와 비공개 메시지는 다음 기준을 따릅니다.

- 공개 가능: 출석 시작/마감 공지, 랭킹, 주간 보고, 공개 프로필
- 비공개 기본: 설정 변경, 권한 부족, 오류, 사유 상세, 점수 수동 조정
- 공개 메시지에는 DB 내부 ID, stack trace, dedup key, 파일 경로, 환경 변수, Token을 표시하지 않습니다.

## Database And Migrations

마이그레이션은 `bot/db/migrations/`의 번호 순서대로 자동 적용됩니다.

현재 주요 migration:

- `001_initial.sql`: 서버 설정, 대원, 출석 세션, 출석 기록, 점수 장부
- `003_excuse_requests.sql`: 사유 신청
- `004_evaluations.sql`: 평가와 수동 점수 조정
- `005_stage_a_voice_verification.sql`: 음성 검증
- `006_stage_b_attendance_adjustments.sql`: 지각 감면과 결석 면제 (기능은 제거됨, 테이블은 이력상 유지)
- `007_stage_c_seasons_achievements_officers.sql`: 시즌, 업적, 칭호, 간부 인사 (기능은 제거됨, 테이블은 이력상 유지)
- `008_excuse_deadline_policy.sql`: 사유 신청 마감 정책, 사유 유형, 승인 처리 메타데이터
- `009_start_announcement_message.sql`: 출석 시작 공지 메시지 ID (마감 시 버튼 잠금용)
- `010_runtime_state_and_verification_waivers.sql`: 스케줄러 하트비트 등 런타임 상태, 검증 면제 사유, 봇 제거 서버 표시
- `011_voice_verification_policy.sql`: 서버별 음성 검증 요구 시간, 마감 시각, 감점

운영 DB 배포 전에는 항상 SQLite 파일을 백업하세요.

### Backup And Reset

- 백업 스케줄러가 하루 한 번 `DATA_DIR/backups/attendance-YYYYMMDD-HHMMSS.db`를 만들고 최근 14개를 보관합니다. 같은 날 백업 파일이 이미 있으면 재시작해도 다시 만들지 않습니다.
- 정상 종료 시 마지막 백업이 1시간보다 오래됐으면 종료 백업을 한 번 더 만듭니다.
- 복원: 봇을 멈추고(`docker compose stop`) 백업 파일을 `data/attendance.db`로 복사한 뒤 다시 시작합니다.
- 전체 초기화: 봇을 멈추고 `data/attendance.db`(및 `-wal`, `-shm`)를 삭제하면 다음 시작 때 빈 DB가 만들어집니다. Discord에서 `/설정 초기화`부터 다시 진행합니다.

## Testing

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.tmp_full -p no:cacheprovider
.\venv\Scripts\python.exe -m ruff check bot tests main.py
```

현재 검증 결과: `156 passed`. GitHub Actions(`.github/workflows/ci.yml`)가 push와 PR마다 ruff와 pytest를 실행합니다.

## Operations Checklist

초기 운영:

- Discord Developer Portal에서 Bot Token을 발급합니다.
- Bot 권한에 `applications.commands`, 메시지 보기/전송, 메시지 기록 보기를 부여합니다. 역할 관리 권한은 필요 없습니다.
- Privileged Intent는 필요 없습니다. **Server Members Intent**를 켜면 대원 탈퇴가 즉시 반영되고, 꺼져 있어도 하루 한 번 REST 조회로 동기화됩니다.
- 음성 검증을 쓸 경우 봇이 대상 음성 채널을 볼 수 있어야 합니다(`/설정 음성검증` 참고).
- `/설정 초기화` 실행 후 `/도움말 카테고리:설정`을 확인합니다.
- `/대원 등록`으로 출석 대상자를 등록합니다.

일일 운영:

- 대원은 출석 공지의 [출석하기] 버튼(또는 `/출석 체크인`)으로 체크인합니다.
- 운영자는 `/출석 현황`으로 미체크 인원을 확인합니다.
- 사유가 있으면 대원이 `/사유 신청` 입력창을 제출하고, 간부는 접수 알림의 [검토하기] 버튼(또는 `/사유 검토`)에서 승인/거절합니다.
- 잘못된 기록은 `/출석 수정`으로 정정합니다.
- 평가와 수동 점수 조정은 `/점수 ...` 그룹을 사용합니다.

장애와 복구:

- 봇이 출석 시간 중에 꺼졌다 켜지면 지난 세션을 마감(결석) 처리하고 마감 공지를 보냅니다.
- 봇이 출석일을 통째로 놓쳤으면(최대 7일) 그 날짜를 "봇 미가동으로 자동 취소" 세션으로 남깁니다. 아무도 체크인할 수 없었으므로 결석·감점을 만들지 않으며, 실제 출석은 `/출석 수정`으로 보정할 수 있습니다.
- 서버를 떠난 대원은 자동으로 제외되고, 봇이 제거된 서버는 스케줄러 대상에서 빠집니다(다시 초대하면 설정이 그대로 복원).
- 세션 스냅샷은 세션 생성 시점(출석일 첫 스케줄러 실행)에 고정됩니다. 그 뒤 등록한 대원은 다음 출석일부터 포함됩니다.
- 글로벌 슬래시 명령은 정의가 바뀐 경우에만 동기화하므로 재시작 반복이 Discord 속도 제한을 유발하지 않습니다.

안전 원칙:

- 기존 점수 이벤트는 수정하지 않고 새 보정 이벤트를 추가합니다.
- 오늘 세션 취소/재개와 평가 취소는 모두 반대 점수 이벤트로 되돌립니다.

## English Summary

Discord Attendance Bot is a community operations bot built with `discord.py` and SQLite.

It supports:

- Guild setup and member registration
- Daily attendance sessions and check-ins
- Excuse requests and officer approvals
- Score ledger and rank calculation
- Public/personal/weekly reports
- Voice attendance verification

Run `/도움말` in Discord to see group-based usage, parameters, and permissions. The project keeps score history append-only.
