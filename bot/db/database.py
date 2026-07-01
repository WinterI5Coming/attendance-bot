"""SQLite 연결과 SQL 마이그레이션을 관리하는 모듈."""

from datetime import datetime, timezone
import logging
from pathlib import Path

import aiosqlite


logger = logging.getLogger(__name__)

MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"


class Database:
    """SQLite 연결과 스키마 마이그레이션을 관리한다.

    매 작업마다 새로운 연결을 열 수 있도록 connect()를 제공하고,
    봇 시작 시 initialize()를 호출해 적용되지 않은 마이그레이션을
    순서대로 실행한다.
    """

    def __init__(self, db_path: Path) -> None:
        """데이터베이스 관리자 객체를 초기화한다.

        Args:
            db_path:
                SQLite 데이터베이스 파일 경로.
        """

        self.db_path = db_path

    async def connect(self) -> aiosqlite.Connection:
        """SQLite 연결을 생성하고 필수 PRAGMA를 적용한다.

        Returns:
            row_factory와 PRAGMA 설정이 적용된 SQLite 연결.

        Raises:
            aiosqlite.Error:
                DB 파일 생성 또는 연결에 실패한 경우.
        """

        # DB 파일이 들어갈 data 폴더가 없으면 자동으로 생성한다.
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = await aiosqlite.connect(
            self.db_path,
        )

        # 조회 결과를 튜플이 아닌 컬럼 이름 기반으로 접근할 수 있게 한다.
        connection.row_factory = aiosqlite.Row

        # SQLite는 기본적으로 외래키 검사를 끄고 시작하기 때문에
        # 연결마다 반드시 활성화해야 한다.
        await connection.execute(
            "PRAGMA foreign_keys = ON;"
        )

        # WAL 모드는 읽기와 쓰기의 동시성을 개선한다.
        await connection.execute(
            "PRAGMA journal_mode = WAL;"
        )

        # DB가 잠겨 있을 때 즉시 실패하지 않고 최대 5초 대기한다.
        await connection.execute(
            "PRAGMA busy_timeout = 5000;"
        )

        return connection

    async def initialize(self) -> None:
        """데이터베이스를 준비하고 미적용 마이그레이션을 실행한다.

        봇 시작 시 한 번 호출한다. 이미 적용된 마이그레이션은
        schema_migrations 기록을 기준으로 건너뛴다.

        Raises:
            RuntimeError:
                마이그레이션 파일명이 규칙에 맞지 않는 경우.
            aiosqlite.Error:
                테이블 생성이나 마이그레이션 실행에 실패한 경우.
        """

        connection = await self.connect()

        try:
            await self._create_migration_table(
                connection,
            )

            await self._apply_pending_migrations(
                connection,
            )
        finally:
            await connection.close()

    async def _create_migration_table(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        """마이그레이션 적용 이력을 저장할 테이블을 생성한다.

        Args:
            connection:
                초기화에 사용할 SQLite 연결.
        """

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            """
        )

        await connection.commit()

    async def _get_applied_versions(
        self,
        connection: aiosqlite.Connection,
    ) -> set[int]:
        """이미 적용된 마이그레이션 버전을 조회한다.

        Args:
            connection:
                조회에 사용할 SQLite 연결.

        Returns:
            적용된 마이그레이션 번호 집합.
        """

        cursor = await connection.execute(
            """
            SELECT version
            FROM schema_migrations
            ORDER BY version;
            """
        )

        rows = await cursor.fetchall()
        await cursor.close()

        return {
            int(row["version"])
            for row in rows
        }

    async def _apply_pending_migrations(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        """아직 적용되지 않은 SQL 파일을 번호 순서대로 실행한다.

        마이그레이션 파일명은 다음 형식을 사용한다.

        예:
            001_initial.sql
            002_indexes.sql

        Args:
            connection:
                마이그레이션 실행에 사용할 SQLite 연결.

        Raises:
            RuntimeError:
                마이그레이션 파일의 버전 번호를 해석할 수 없는 경우.
            aiosqlite.Error:
                SQL 실행에 실패한 경우.
        """

        applied_versions = await self._get_applied_versions(
            connection,
        )

        migration_files = sorted(
            MIGRATIONS_DIRECTORY.glob("*.sql")
        )

        for migration_file in migration_files:
            version_text = migration_file.stem.split(
                "_",
                maxsplit=1,
            )[0]

            try:
                version = int(version_text)
            except ValueError as exc:
                raise RuntimeError(
                    "마이그레이션 파일명은 숫자로 시작해야 합니다: "
                    f"{migration_file.name}"
                ) from exc

            if version in applied_versions:
                continue

            sql = migration_file.read_text(
                encoding="utf-8",
            )

            applied_at = datetime.now(
                timezone.utc,
            ).isoformat()

            # 파일명은 프로젝트 내부에서 관리하지만 SQL 문자열에
            # 포함되므로 작은따옴표를 이스케이프한다.
            escaped_name = migration_file.name.replace(
                "'",
                "''",
            )

            # 스키마 변경과 마이그레이션 이력 기록을 하나의
            # 트랜잭션 안에서 실행한다.
            migration_script = f"""
            BEGIN IMMEDIATE;

            {sql}

            INSERT INTO schema_migrations (
                version,
                name,
                applied_at
            )
            VALUES (
                {version},
                '{escaped_name}',
                '{applied_at}'
            );

            COMMIT;
            """

            try:
                await connection.executescript(
                    migration_script,
                )
            except Exception:
                # SQL 중간에 실패하면 열린 트랜잭션을 되돌린다.
                await connection.rollback()

                logger.exception(
                    "마이그레이션 실행 실패: %s",
                    migration_file.name,
                )

                raise

            logger.info(
                "마이그레이션 적용 완료: %s",
                migration_file.name,
            )