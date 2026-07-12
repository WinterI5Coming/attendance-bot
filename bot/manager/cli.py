"""Command-line interface for AttendanceBotManager."""

from __future__ import annotations

import argparse
from pathlib import Path

from bot.manager.database_service import DatabaseManagerService
from bot.manager.reset_service import RESET_CONFIRMATION, DataResetService


def run_cli(argv: list[str], service: DatabaseManagerService) -> int:
    """Run manager CLI commands. Return 0 on success."""

    parser = argparse.ArgumentParser(description="AttendanceBot database manager")
    parser.add_argument("--backup", action="store_true", help="Create a backup")
    parser.add_argument("--restore", type=Path, help="Restore a backup DB file")
    parser.add_argument("--validate", type=Path, help="Validate a backup DB file")
    parser.add_argument("--list-backups", action="store_true", help="List backups")
    parser.add_argument(
        "--reset-all-data",
        action="store_true",
        help="Backup then delete all operational data",
    )
    args = parser.parse_args(argv)

    if args.backup:
        result = service.create_backup()
        print(f"Backup created: {result.backup_path}")
        print(f"Metadata: {result.metadata_path}")
        return 0

    if args.restore:
        result = service.restore_backup(args.restore)
        print("Restore completed.")
        print(f"Restored from: {result.restored_from}")
        print(f"Current DB: {result.current_database}")
        print(f"Pre-restore backup: {result.pre_restore_backup}")
        return 0

    if args.validate:
        print(service.validate_backup(args.validate))
        return 0

    if args.list_backups:
        backups = service.list_backups()
        if not backups:
            print("No backups found.")
        for backup in backups:
            print(backup)
        return 0

    if args.reset_all_data:
        print(
            "경고: 모든 출석 기록, 사용자 정보, 점수, 설정, 사유 신청 데이터가 삭제됩니다.\n\n"
            "초기화 전 전체 DB가 자동으로 백업됩니다.\n"
            "이 작업은 자동으로 되돌릴 수 없습니다.\n"
        )
        confirmation = input("계속하려면 RESET ALL DATA를 입력하세요: ")
        if confirmation != RESET_CONFIRMATION:
            print("확인 문구가 일치하지 않아 초기화를 취소했습니다.")
            return 3

        reset_service = DataResetService(
            database_path=service.database_path,
            backups_directory=service.backups_directory,
        )
        result = reset_service.reset_all_data()
        print("데이터 초기화가 완료되었습니다.")
        print(f"백업 파일: {result.backup_path}")
        print(f"메타데이터: {result.metadata_path}")
        print(f"완료 시각: {result.completed_at.isoformat()}")
        print("새 정책 적용: 사유 신청 마감은 출석일 1일 전 23:00, 관리자 승인 필수")
        print("다음 실행 시 /초기설정으로 서버별 출석 설정을 다시 등록하세요.")
        print("삭제된 레코드 수:")
        for table, count in result.deleted_counts.items():
            print(f"- {table}: {count}")
        return 0

    return 2
