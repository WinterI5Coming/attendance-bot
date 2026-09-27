# Discord Attendance Bot

> Discord 서버의 출석, 사유 신청, 점수, 리포트를 SQLite 기반으로 관리하는 근태관리봇입니다.  
> A SQLite-backed Discord attendance bot for check-ins, excuses, scores, and reports.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Discord.py](https://img.shields.io/badge/discord.py-slash%20commands-5865F2)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57)
![Tests](https://img.shields.io/badge/tests-121%20passed-brightgreen)

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
- Discord 역할 변경은 DB 저장 이후에 수행하고, 성공/실패 이력을 별도로 남깁니다.

## Features

- 서버별 초기 설정: 간부 역할, 출석 채널, 공지 채널, 출석 요일과 시간
- 대원 관리: 등록, 제외, 활성 대원 목록 조회
- 출석 세션 자동 운영: 세션 생성, 시작 공지, 마감 처리, 재시작 복구
- 출석 체크: 출석 공지의 **[출석하기] 버튼** 한 번으로 체크인 (정상/지각/사유 판정, 마감 시 버튼 잠금)
- 사유 신청: **입력창(모달)** 으로 신청 → 간부에게 접수 알림 → **[검토하기] 버튼**으로 승인/거절
- 점수 장부: 출석 점수, 보정 점수, 평가 점수, 수동 조정, 취소 보정
- 리포트: 내 정보, 공개 리포트, 랭킹, 주간 보고
- Stage A: 음성 채널 체류 기반 출석 검증
- 상세 도움말: Discord 안에서 `/도움말`로 명령 사용법 확인

## Command Guide

명령은 기능별 그룹(`/그룹 하위명령`)으로 묶여 있으며, 봇 안에서 가장 자세한 사용법은 `/도움말` 명령으로 확인할 수 있습니다.

```text
/도움말
/도움말 카테고리:설정 | 대원 | 출석 | 사유 | 리포트 | 점수
```

| Group | Commands | Permission |
| --- | --- | --- |
| `/설정` | `초기화`(관리자), `조회`, `변경`, `출석시간`(관리자) | Officer/admin |
| `/대원` | `등록`, `제외` | Officer/admin · `목록` Everyone |
| 공지 버튼 | 출석 공지의 **[✅ 출석하기]**, 접수 알림의 **[🗂️ 검토하기]** | Member / Officer |
| `/출석` | `체크인`(대원, 버튼과 동일), `현황`(모두), `수정`, `오늘취소`, `오늘재개` | Officer/admin |
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

## Excuse Deadline Policy

기본 사유 신청 정책은 `Asia/Seoul` 기준 출석일 전날 23:00까지 신청, 관리자 승인 필수, 마감 이후 일반 사용자 신청 불가입니다.

- 사용자는 `/사유 신청`에서 `결석`, `지각`, `조퇴` 유형을 선택해 신청합니다.
- 신청은 `PENDING` 상태로 생성되고 `/사유 승인` 이후 출석 판정과 점수에 반영됩니다.
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

버튼은 `timeout=None` 영속 뷰로 등록되어 봇을 재시작해도 과거 공지의 버튼이 계속 동작합니다.

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

운영 DB 배포 전에는 항상 SQLite 파일을 백업하세요.

### Backup And Reset

- 백업 스케줄러가 하루 한 번 `DATA_DIR/backups/attendance-YYYYMMDD-HHMMSS.db`를 만들고 최근 14개를 보관합니다.
- 복원: 봇을 멈추고(`docker compose stop`) 백업 파일을 `data/attendance.db`로 복사한 뒤 다시 시작합니다.
- 전체 초기화: 봇을 멈추고 `data/attendance.db`(및 `-wal`, `-shm`)를 삭제하면 다음 시작 때 빈 DB가 만들어집니다. Discord에서 `/설정 초기화`부터 다시 진행합니다.

## Testing

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=.tmp_full -p no:cacheprovider
.\venv\Scripts\python.exe -m ruff check bot tests main.py
```

현재 검증 결과: `121 passed`

## Operations Checklist

초기 운영:

- Discord Developer Portal에서 Bot Token을 발급합니다.
- Bot 권한에 `applications.commands`, 메시지 전송, 역할 관리 권한을 부여합니다.
- Stage A 음성 검증을 사용할 경우 voice state intent를 활성화합니다.
- `/설정 초기화` 실행 후 `/도움말 카테고리:설정`을 확인합니다.
- `/대원 등록`으로 출석 대상자를 등록합니다.

일일 운영:

- 대원은 출석 공지의 [출석하기] 버튼(또는 `/출석 체크인`)으로 체크인합니다.
- 운영자는 `/출석 현황`으로 미체크 인원을 확인합니다.
- 사유가 있으면 대원이 `/사유 신청` 입력창을 제출하고, 간부는 접수 알림의 [검토하기] 버튼(또는 `/사유 검토`)에서 승인/거절합니다.
- 잘못된 기록은 `/출석 수정`으로 정정합니다.
- 평가와 수동 점수 조정은 `/점수 ...` 그룹을 사용합니다.

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
