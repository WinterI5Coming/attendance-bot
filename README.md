# Discord Attendance Bot

> Discord 서버의 출석, 사유 신청, 점수, 시즌, 업적, 간부 인사를 SQLite 기반으로 관리하는 근태관리봇입니다.  
> A SQLite-backed Discord attendance bot for check-ins, excuses, scores, seasons, achievements, and officer reviews.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Discord.py](https://img.shields.io/badge/discord.py-slash%20commands-5865F2)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57)
![Tests](https://img.shields.io/badge/tests-99%20passed-brightgreen)

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

The bot is designed for communities that need repeatable attendance operations: daily check-in sessions, officer approvals, attendance corrections, score ledgers, seasonal rankings, achievements, and audited role changes.

핵심 원칙:

- 기존 점수 기록은 삭제하거나 수정하지 않고 `score_events`에 보정 이벤트를 추가합니다.
- 마이그레이션은 버전 순서대로 추가하며 기존 migration 파일을 수정하지 않습니다.
- Discord 역할 변경은 DB 저장 이후에 수행하고, 성공/실패 이력을 별도로 남깁니다.
- 개인 점수 계급과 Discord 간부/대원 역할은 서로 다른 정책으로 분리합니다.

## Features

- 서버별 초기 설정: 간부 역할, 출석 채널, 공지 채널, 출석 요일과 시간
- 대원 관리: 등록, 제외, 활성 대원 목록 조회
- 출석 세션 자동 운영: 세션 생성, 시작 공지, 마감 처리, 재시작 복구
- 출석 체크: 정상 출석, 지각, 결석, 사유 지각/결석
- 사유 신청: 신청, 취소, 승인, 거절, 감사 로그
- 점수 장부: 출석 점수, 보정 점수, 평가 점수, 수동 조정, 취소 보정
- 리포트: 내 정보, 공개 리포트, 랭킹, 주간 보고
- Stage A: 음성 채널 체류 기반 출석 검증
- Stage B: 지각 감면, 결석 면제, 통계 반영
- Stage C: 시즌, 시즌 랭킹, 업적, 칭호, 업적 역할, 간부 인사 미리보기/실행
- 상세 도움말: Discord 안에서 `/도움말`로 명령 사용법 확인

## Command Guide

봇 안에서 가장 자세한 사용법은 `/도움말` 명령으로 확인할 수 있습니다.

```text
/도움말
/도움말 카테고리:시작하기
/도움말 카테고리:출석
/도움말 카테고리:사유 신청
/도움말 카테고리:리포트와 점수
/도움말 카테고리:감면과 면제
/도움말 카테고리:시즌과 업적
/도움말 카테고리:간부 인사
```

대표 명령:

| Category | Command | Permission | Purpose |
| --- | --- | --- | --- |
| Setup | `/초기설정` | Server admin | 서버의 기본 역할과 채널을 설정합니다. |
| Setup | `/대원등록` | Officer/admin | Discord 사용자를 출석 대상자로 등록합니다. |
| Attendance | `/출석` | Registered member | 오늘 출석 세션에 체크인합니다. |
| Attendance | `/출석현황` | Everyone | 오늘 출석 현황을 조회합니다. |
| Attendance | `/출석수정` | Officer/admin | 특정 날짜의 출석 기록을 정정합니다. |
| Excuse | `/사유신청` | Registered member | 지각/결석 사유를 신청합니다. |
| Excuse | `/사유승인` | Officer/admin | 대기 중인 사유 신청을 승인합니다. |
| Report | `/내정보` | Registered member | 내 점수, 계급, 출석률을 조회합니다. |
| Report | `/랭킹` | Everyone | 서버 점수 랭킹을 조회합니다. |
| Stage B | `/지각감면` | Officer/admin | 승인 사유 기반 지각 시간을 감면합니다. |
| Stage B | `/결석면제` | Officer/admin | 승인 사유 기반 결석 감점을 면제합니다. |
| Stage C | `/시즌생성` | Officer/admin | 새 시즌을 생성합니다. |
| Stage C | `/시즌랭킹` | Everyone | 시즌별 랭킹을 조회합니다. |
| Stage C | `/업적평가` | Officer/admin | 시즌 통계 기준 업적과 보상을 지급합니다. |
| Stage C | `/간부인사미리보기` | Officer/admin | 역할 변경 없이 인사안을 저장합니다. |
| Stage C | `/간부인사실행` | Server admin | 저장된 인사안을 실제 역할 변경으로 적용합니다. |

## Architecture

```text
Discord slash commands
        |
        v
bot/cogs/*          사용자 입력 검증, 권한 확인, 응답 메시지 구성
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
| `bot/cogs/` | Discord slash command handlers |
| `bot/services/` | Business rules and orchestration |
| `bot/repositories/` | SQLite data access layer |
| `bot/policies/` | Score and rank policies |
| `bot/scheduler/` | Attendance and backup background loops |
| `bot/db/migrations/` | Versioned SQLite migrations |
| `tests/` | Unit and integration tests |

## Quick Start

### 1. Requirements

- Python 3.11 이상
- Discord Application과 Bot Token
- 테스트용 Discord 서버 ID

### 2. Install

```powershell
git clone <repository-url>
cd attendance-bot

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configure

```powershell
Copy-Item .env.example .env
```

`.env`에 다음 값을 입력합니다.

```env
DISCORD_TOKEN=your_discord_bot_token
DEVELOPMENT_GUILD_ID=your_test_guild_id
DB_PATH=data/attendance.db
TIMEZONE=Asia/Seoul
LOG_LEVEL=INFO
DEFAULT_ATTENDANCE_DAYS=MON,TUE,WED,THU,FRI
DEFAULT_ATTENDANCE_START=21:30
DEFAULT_LATE_DEADLINE=21:40
DEFAULT_CLOSE_DEADLINE=21:45
DEFAULT_EXCUSE_MODE=officer_approval
ENABLE_SEASONS=false
```

### 4. Run

```powershell
.\venv\Scripts\python.exe -m bot.main
```

처음 실행하면 다음 작업이 자동으로 진행됩니다.

1. SQLite DB 파일과 `data/` 디렉터리를 준비합니다.
2. `bot/db/migrations/*.sql`을 버전 순서대로 적용합니다.
3. Cog를 등록하고 `DEVELOPMENT_GUILD_ID` 서버에 slash command를 동기화합니다.
4. 누락된 출석 마감 처리를 복구합니다.
5. 출석/백업 스케줄러를 시작합니다.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | - | Discord Bot Token |
| `DEVELOPMENT_GUILD_ID` | Yes | - | Slash command sync target guild |
| `DB_PATH` | No | `data/attendance.db` | SQLite database path |
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
| `ENABLE_SEASONS` | No | `false` | Enables season and officer-review slash commands |

## Excuse Deadline Policy

기본 사유 신청 정책은 `Asia/Seoul` 기준 출석일 전날 23:00까지 신청, 관리자 승인 필수, 마감 이후 일반 사용자 신청 불가입니다.

- 사용자는 `/사유신청`에서 `결석`, `지각`, `조퇴` 유형을 선택해 신청합니다.
- 신청은 `PENDING` 상태로 생성되고 `/사유승인` 이후 출석 판정과 점수에 반영됩니다.
- 마감 이후 긴급 예외는 관리자/간부가 `/사유예외등록`으로 등록합니다.
- 정책 확인은 `/사유정책조회`, 공개 공지는 `/사유정책공지`, 마감 시간 변경은 `/사유정책설정`을 사용합니다.
- 환경 기본값은 새 서버 초기 설정에 적용되며, 이미 생성된 서버는 `/사유정책설정`으로 변경합니다.

## Message Design

봇 응답은 `bot/ui/` 계층을 기준으로 통일합니다.

- `bot/ui/message_theme.py`: 성공, 정보, 경고, 오류, 관리자 메시지 색상
- `bot/ui/embed_factory.py`: 표준 Embed 생성 규칙
- `bot/ui/formatters.py`: 점수, 출석 상태, 검증 상태, 날짜/시간 표시

공개 메시지와 비공개 메시지는 다음 기준을 따릅니다.

- 공개 가능: 출석 시작/마감 공지, 랭킹, 주간 보고, 공개 프로필
- 비공개 기본: 설정 변경, 권한 부족, 오류, 사유 상세, 점수 수동 조정, 업적 역할 설정, 간부 인사 미리보기
- 공개 메시지에는 DB 내부 ID, stack trace, dedup key, 파일 경로, 환경 변수, Token을 표시하지 않습니다.

## Database And Migrations

마이그레이션은 `bot/db/migrations/`의 번호 순서대로 자동 적용됩니다.

현재 주요 migration:

- `001_initial.sql`: 서버 설정, 대원, 출석 세션, 출석 기록, 점수 장부
- `003_excuse_requests.sql`: 사유 신청
- `004_evaluations.sql`: 평가와 수동 점수 조정
- `005_stage_a_voice_verification.sql`: 음성 검증
- `006_stage_b_attendance_adjustments.sql`: 지각 감면과 결석 면제
- `007_stage_c_seasons_achievements_officers.sql`: 시즌, 업적, 칭호, 간부 인사
- `008_excuse_deadline_policy.sql`: 사유 신청 마감 정책, 사유 유형, 승인 처리 메타데이터

운영 DB 배포 전에는 항상 SQLite 파일을 백업하세요.

### Full Data Reset

운영 데이터 전체 초기화는 명시적으로 실행해야 하며, 실행 직전에 현재 SQLite DB가 `backups/before_policy_reset_YYYYMMDD_HHMMSS.db`로 자동 백업됩니다. 이 명령은 출석 기록, 사용자, 점수, 서버 설정, 사유 신청 등 운영 테이블을 비우지만 마이그레이션 이력은 보존합니다.

```powershell
AttendanceBotManager.exe --reset-all-data
```

또는 소스 실행 환경에서는 다음 명령을 사용합니다.

```powershell
.\venv\Scripts\python.exe manager_main.py --reset-all-data
```

실행 후 정확히 `RESET ALL DATA`를 입력해야 초기화가 진행됩니다. 문구가 다르면 작업은 취소됩니다.

## Season Feature Status

시즌 기능은 현재 **기본 비활성화** 상태입니다.

- 기본값: `ENABLE_SEASONS=false`
- 비활성화 시 등록하지 않는 명령: `/시즌생성`, `/시즌목록`, `/시즌시작`, `/시즌종료`, `/시즌취소`, `/시즌재집계`, `/시즌랭킹`, `/간부인사미리보기`, `/간부인사실행`
- 보존되는 데이터: `seasons`, `season_member_stats`, `officer_reviews`, `officer_role_change_logs`
- 계속 사용 가능한 기능: 기존 업적 조회, 칭호 조회, 칭호 장착/해제, 사용자 프로필
- 활성화 방법: staging guild에서 검증한 뒤 `.env`에 `ENABLE_SEASONS=true`를 설정하고 봇을 재시작합니다.

## Achievements And Titles

일반 사용자 흐름:

1. `/업적안내`로 업적과 칭호 사용법을 확인합니다.
2. `/내업적`으로 획득한 업적을 확인합니다.
3. `/내칭호`로 보유 칭호와 현재 장착 칭호를 확인합니다.
4. `/칭호장착`에서 자동완성으로 보유 칭호를 선택합니다.
5. `/사용자프로필`로 공개 가능한 업적/칭호 요약을 확인합니다.

관리자 흐름:

1. `/업적초기화`로 기본 업적 정의를 준비합니다.
2. `/업적목록`에서 업적 코드와 보상 점수를 확인합니다.
3. `/업적역할설정`으로 특정 업적과 Discord 역할을 연결합니다.
4. `/업적역할목록`으로 현재 매핑을 확인합니다.
5. 시즌 기능이 활성화된 서버에서만 `/업적평가`로 신규 업적 지급을 실행합니다.

중요한 제약:

- 칭호는 한 번에 하나만 장착할 수 있습니다.
- 칭호 장착/해제는 점수에 영향을 주지 않습니다.
- 업적 보상 점수는 기존 점수 이벤트를 수정하지 않고 새 `ACHIEVEMENT_REWARD` 이벤트로 추가됩니다.
- 역할 부여가 실패해도 업적 획득 자체는 취소하지 않습니다. Discord 역할 권한과 역할 계층을 확인하세요.

## Testing

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.tmp_full -p no:cacheprovider
.\venv\Scripts\python.exe -m compileall -q bot tests
.\venv\Scripts\python.exe -m pip check
```

현재 검증 결과:

```text
124 passed
No broken requirements found.
```

## Operations Checklist

초기 운영:

- Discord Developer Portal에서 Bot Token을 발급합니다.
- Bot 권한에 `applications.commands`, 메시지 전송, 역할 관리 권한을 부여합니다.
- Stage A 음성 검증을 사용할 경우 voice state intent를 활성화합니다.
- `/초기설정` 실행 후 `/도움말 카테고리:시작하기`를 확인합니다.
- `/대원등록`으로 출석 대상자를 등록합니다.

일일 운영:

- 대원은 `/출석`으로 체크인합니다.
- 운영자는 `/출석현황`으로 미체크 인원을 확인합니다.
- 사유가 있으면 `/사유신청`, `/사유승인`, `/사유거절` 흐름을 사용합니다.
- 잘못된 기록은 `/출석수정`으로 정정합니다.

시즌 운영:

- `/시즌생성`으로 시즌을 만들고 `/시즌시작`으로 활성화합니다.
- 필요할 때 `/시즌재집계`로 Stage A/B 반영 통계를 다시 계산합니다.
- `/업적초기화`, `/업적역할설정`, `/업적평가`로 업적 보상을 관리합니다.
- `/간부인사미리보기`로 먼저 확인하고, 서버 관리자만 `/간부인사실행`을 수행합니다.

안전 원칙:

- Preview 명령은 Discord 역할을 변경하지 않습니다.
- 역할 변경 결과는 `officer_role_change_logs`에 남습니다.
- 서버 소유자와 관리자 계정은 간부 인사 보호 대상으로 취급됩니다.
- 기존 점수 이벤트는 수정하지 않고 새 보정 이벤트를 추가합니다.

## English Summary

Discord Attendance Bot is a community operations bot built with `discord.py` and SQLite.

It supports:

- Guild setup and member registration
- Daily attendance sessions and check-ins
- Excuse requests and officer approvals
- Score ledger and rank calculation
- Public/personal/weekly reports
- Voice attendance verification
- Late reduction and absence exemption
- Seasons, achievements, titles, and achievement role rewards
- Officer review preview and audited role execution

Run `/help` equivalent command `/도움말` in Discord to see category-based usage, parameters, and permissions. The project keeps score history append-only and applies Discord role changes only after database state is committed.
