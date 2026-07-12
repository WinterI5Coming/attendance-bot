"""Stage C 시즌, 업적, 칭호, 간부 인사 데이터를 다루는 SQLite Repository."""

from typing import Any

import aiosqlite

from bot.db.database import Database


class StageCRepository:
    """Stage C 테이블에 대한 조회와 저장 쿼리를 캡슐화한다."""

    def __init__(self, database: Database) -> None:
        """Repository가 사용할 Database 객체를 저장한다."""

        self.database = database

    async def create_season(
        self,
        *,
        guild_id: str,
        name: str,
        start_date: str,
        end_date: str,
        policy_snapshot_json: str,
        created_by_discord_id: str | None,
        now: str,
    ) -> int:
        """새 시즌을 예약 상태로 생성하고 생성된 ID를 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                INSERT INTO seasons (
                    guild_id,
                    name,
                    start_date,
                    end_date,
                    status,
                    policy_snapshot_json,
                    created_by_discord_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'SCHEDULED', ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    name.strip(),
                    start_date,
                    end_date,
                    policy_snapshot_json,
                    created_by_discord_id,
                    now,
                    now,
                ),
            )
            await connection.commit()
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_season(
        self,
        *,
        guild_id: str,
        season_id: int,
    ) -> dict[str, Any] | None:
        """서버 ID와 시즌 ID로 단일 시즌을 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM seasons
                WHERE guild_id = ? AND id = ?;
                """,
                (guild_id, season_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            await connection.close()

    async def get_active_season(self, *, guild_id: str) -> dict[str, Any] | None:
        """서버의 현재 활성 시즌을 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM seasons
                WHERE guild_id = ? AND status = 'ACTIVE'
                ORDER BY started_at DESC, id DESC
                LIMIT 1;
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            await connection.close()

    async def list_seasons(self, *, guild_id: str) -> list[dict[str, Any]]:
        """서버에 등록된 모든 시즌을 최신순으로 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM seasons
                WHERE guild_id = ?
                ORDER BY start_date DESC, id DESC;
                """,
                (guild_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def update_season_status(
        self,
        *,
        guild_id: str,
        season_id: int,
        from_statuses: tuple[str, ...],
        to_status: str,
        now: str,
        cancellation_reason: str | None = None,
    ) -> bool:
        """허용된 이전 상태에서 목표 상태로 시즌 상태를 전환한다."""

        timestamp_column = {
            "ACTIVE": "started_at",
            "CLOSED": "closed_at",
            "CANCELLED": "cancelled_at",
        }.get(to_status)

        placeholders = ", ".join("?" for _ in from_statuses)
        set_parts = [
            "status = ?",
            "stats_dirty = 1",
            "updated_at = ?",
        ]
        params: list[Any] = [to_status, now]

        if timestamp_column is not None:
            set_parts.append(f"{timestamp_column} = ?")
            params.append(now)

        if to_status == "CANCELLED":
            set_parts.append("cancellation_reason = ?")
            params.append(cancellation_reason)

        params.extend([guild_id, season_id, *from_statuses])

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                f"""
                UPDATE seasons
                SET {", ".join(set_parts)}
                WHERE guild_id = ?
                  AND id = ?
                  AND status IN ({placeholders});
                """,
                tuple(params),
            )
            await connection.commit()
            return cursor.rowcount > 0
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def calculate_season_stats(
        self,
        *,
        guild_id: str,
        season_id: int,
    ) -> list[dict[str, Any]]:
        """시즌 기간 안의 출석, 음성 검증, 점수 데이터를 멤버별로 집계한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    m.id AS member_id,
                    m.discord_id,
                    m.display_name,
                    COUNT(asm.id) AS target_session_count,
                    SUM(CASE
                        WHEN abs_adj.id IS NOT NULL
                             AND gs.exempt_absence_counts_in_attendance_denominator = 0
                            THEN 0
                        ELSE 1
                    END) AS attendance_denominator,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'PRESENT' THEN 1
                        WHEN abs_adj.id IS NULL AND ar.status = 'PRESENT' THEN 1
                        ELSE 0
                    END) AS present_count,
                    SUM(CASE
                        WHEN late_adj.resulting_status = 'LATE' THEN 1
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'LATE' THEN 1
                        ELSE 0
                    END) AS late_count,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent_count,
                    SUM(CASE WHEN abs_adj.id IS NOT NULL THEN 1 ELSE 0 END) AS exempt_absent_count,
                    SUM(CASE
                        WHEN late_adj.id IS NULL AND abs_adj.id IS NULL AND ar.status = 'EXCUSED_LATE' THEN 1
                        ELSE 0
                    END) AS excused_late_count,
                    SUM(CASE WHEN abs_adj.id IS NULL AND ar.status = 'EXCUSED_ABSENT' THEN 1 ELSE 0 END) AS excused_absent_count,
                    (
                        SELECT COALESCE(SUM(vpl.duration_seconds), 0)
                        FROM voice_presence_logs AS vpl
                        JOIN attendance_sessions AS vs ON vs.id = vpl.session_id
                        JOIN seasons AS vseason ON vseason.id = ?
                        WHERE vpl.member_id = m.id
                          AND vs.guild_id = ?
                          AND vs.attendance_date BETWEEN vseason.start_date AND vseason.end_date
                          AND vs.status != 'CANCELLED'
                    ) AS voice_seconds,
                    (
                        SELECT COUNT(*)
                        FROM attendance_verifications AS av
                        JOIN attendance_records AS avr ON avr.id = av.attendance_record_id
                        JOIN attendance_sessions AS avs ON avs.id = avr.session_id
                        JOIN seasons AS avseason ON avseason.id = ?
                        WHERE avr.member_id = m.id
                          AND avs.guild_id = ?
                          AND avs.attendance_date BETWEEN avseason.start_date AND avseason.end_date
                          AND av.status = 'VERIFIED'
                    ) AS voice_verified_count,
                    (
                        SELECT COUNT(*)
                        FROM attendance_verifications AS av
                        JOIN attendance_records AS avr ON avr.id = av.attendance_record_id
                        JOIN attendance_sessions AS avs ON avs.id = avr.session_id
                        JOIN seasons AS avseason ON avseason.id = ?
                        WHERE avr.member_id = m.id
                          AND avs.guild_id = ?
                          AND avs.attendance_date BETWEEN avseason.start_date AND avseason.end_date
                          AND av.status = 'FAILED'
                    ) AS voice_failed_count,
                    (
                        SELECT COALESCE(SUM(se.delta), 0)
                        FROM score_events AS se
                        JOIN seasons AS ss ON ss.id = ?
                        WHERE se.member_id = m.id
                          AND se.guild_id = ?
                          AND substr(se.created_at, 1, 10) BETWEEN ss.start_date AND ss.end_date
                    ) AS season_score
                FROM seasons AS season
                JOIN attendance_sessions AS s
                    ON s.guild_id = season.guild_id
                    AND s.attendance_date BETWEEN season.start_date AND season.end_date
                    AND s.status != 'CANCELLED'
                JOIN attendance_session_members AS asm ON asm.session_id = s.id
                JOIN members AS m ON m.id = asm.member_id
                JOIN guild_settings AS gs ON gs.guild_id = s.guild_id
                LEFT JOIN attendance_records AS ar
                    ON ar.session_id = asm.session_id
                    AND ar.member_id = asm.member_id
                LEFT JOIN attendance_adjustments AS late_adj
                    ON late_adj.attendance_record_id = ar.id
                    AND late_adj.adjustment_type = 'LATE_REDUCTION'
                    AND late_adj.status = 'ACTIVE'
                LEFT JOIN attendance_adjustments AS abs_adj
                    ON abs_adj.attendance_record_id = ar.id
                    AND abs_adj.adjustment_type = 'ABSENCE_EXEMPTION'
                    AND abs_adj.status = 'ACTIVE'
                WHERE season.guild_id = ?
                  AND season.id = ?
                GROUP BY m.id
                ORDER BY season_score DESC, present_count DESC, m.display_name COLLATE NOCASE;
                """,
                (
                    season_id,
                    guild_id,
                    season_id,
                    guild_id,
                    season_id,
                    guild_id,
                    season_id,
                    guild_id,
                    guild_id,
                    season_id,
                ),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def replace_season_stats(
        self,
        *,
        season_id: int,
        stats: list[dict[str, Any]],
        finalized: bool,
        now: str,
    ) -> None:
        """기존 시즌 통계 스냅샷을 삭제하고 새 집계 결과로 교체한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                "DELETE FROM season_member_stats WHERE season_id = ?;",
                (season_id,),
            )
            for row in stats:
                await connection.execute(
                    """
                    INSERT INTO season_member_stats (
                        season_id,
                        member_id,
                        target_session_count,
                        attendance_denominator,
                        present_count,
                        late_count,
                        early_leave_count,
                        no_participation_count,
                        absent_count,
                        exempt_absent_count,
                        attendance_rate,
                        on_time_rate,
                        voice_seconds,
                        voice_verified_count,
                        voice_failed_count,
                        current_streak,
                        best_streak,
                        season_score,
                        final_personal_rank,
                        officer_evaluation_score,
                        finalized_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        season_id,
                        row["member_id"],
                        row["target_session_count"],
                        row["attendance_denominator"],
                        row["present_count"],
                        row["late_count"],
                        row["absent_count"],
                        row["exempt_absent_count"],
                        row["attendance_rate"],
                        row["on_time_rate"],
                        row["voice_seconds"],
                        row["voice_verified_count"],
                        row["voice_failed_count"],
                        row["current_streak"],
                        row["best_streak"],
                        row["season_score"],
                        row["final_personal_rank"],
                        row["officer_evaluation_score"],
                        now if finalized else None,
                        now,
                        now,
                    ),
                )
            await connection.execute(
                """
                UPDATE seasons
                SET stats_dirty = 0,
                    last_reconciled_at = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (now, now, season_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_season_stats(
        self,
        *,
        season_id: int,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """시즌 통계 스냅샷을 랭킹 순서로 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT sms.*, m.discord_id, m.display_name
                FROM season_member_stats AS sms
                JOIN members AS m ON m.id = sms.member_id
                WHERE sms.season_id = ?
                ORDER BY sms.season_score DESC,
                         sms.attendance_rate DESC,
                         sms.present_count DESC,
                         m.display_name COLLATE NOCASE
                LIMIT ?;
                """,
                (season_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def upsert_default_achievements(
        self,
        *,
        guild_id: str,
        definitions: list[dict[str, Any]],
        now: str,
    ) -> None:
        """서버 기본 업적 정의를 생성하거나 최신 값으로 갱신한다."""

        connection = await self.database.connect()
        try:
            for definition in definitions:
                await connection.execute(
                    """
                    INSERT INTO achievement_definitions (
                        guild_id,
                        code,
                        name,
                        description,
                        condition_type,
                        threshold,
                        reward_score,
                        title_name,
                        once_per_season,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, code) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        condition_type = excluded.condition_type,
                        threshold = excluded.threshold,
                        reward_score = excluded.reward_score,
                        title_name = excluded.title_name,
                        once_per_season = excluded.once_per_season,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        guild_id,
                        definition["code"],
                        definition["name"],
                        definition["description"],
                        definition["condition_type"],
                        definition["threshold"],
                        definition["reward_score"],
                        definition.get("title_name"),
                        1 if definition["once_per_season"] else 0,
                        now,
                        now,
                    ),
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_achievement_definitions(
        self,
        *,
        guild_id: str,
    ) -> list[dict[str, Any]]:
        """활성 업적 정의 목록을 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM achievement_definitions
                WHERE guild_id = ? AND is_active = 1
                ORDER BY id;
                """,
                (guild_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def award_achievement(
        self,
        *,
        guild_id: str,
        member_id: int,
        definition: dict[str, Any],
        season_id: int | None,
        created_by_discord_id: str | None,
        now: str,
    ) -> bool:
        """멤버에게 업적, 보상 점수, 칭호를 중복 없이 지급한다."""

        connection = await self.database.connect()
        try:
            score_event_id = None
            if definition["reward_score"]:
                cursor = await connection.execute(
                    """
                    INSERT OR IGNORE INTO score_events (
                        guild_id,
                        member_id,
                        event_type,
                        delta,
                        reference_type,
                        reference_id,
                        dedup_key,
                        description,
                        created_by_discord_id,
                        created_at,
                        reversed_event_id
                    )
                    VALUES (?, ?, 'ACHIEVEMENT_REWARD', ?, 'ACHIEVEMENT', ?, ?, ?, ?, ?, NULL);
                    """,
                    (
                        guild_id,
                        member_id,
                        definition["reward_score"],
                        definition["id"],
                        self._achievement_dedup_key(member_id, definition["id"], season_id),
                        f"Achievement reward: {definition['name']}",
                        created_by_discord_id,
                        now,
                    ),
                )
                if cursor.rowcount:
                    score_event_id = cursor.lastrowid

            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO member_achievements (
                    guild_id,
                    member_id,
                    achievement_definition_id,
                    season_id,
                    status,
                    earned_at,
                    score_event_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'EARNED', ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    member_id,
                    definition["id"],
                    season_id,
                    now,
                    score_event_id,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount > 0

            if created and definition.get("title_name"):
                title_cursor = await connection.execute(
                    """
                    INSERT INTO title_definitions (
                        guild_id,
                        title_name,
                        source_achievement_definition_id,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, title_name) DO UPDATE SET
                        source_achievement_definition_id = excluded.source_achievement_definition_id,
                        is_active = 1,
                        updated_at = excluded.updated_at;
                    """,
                    (
                        guild_id,
                        definition["title_name"],
                        definition["id"],
                        now,
                        now,
                    ),
                )
                title_id = title_cursor.lastrowid
                if not title_id:
                    cursor = await connection.execute(
                        """
                        SELECT id
                        FROM title_definitions
                        WHERE guild_id = ? AND title_name = ?;
                        """,
                        (guild_id, definition["title_name"]),
                    )
                    title_row = await cursor.fetchone()
                    await cursor.close()
                    assert title_row is not None
                    title_id = title_row["id"]
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO member_titles (
                        guild_id,
                        member_id,
                        title_definition_id,
                        unlocked_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (guild_id, member_id, title_id, now, now, now),
                )

            await connection.commit()
            return created
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_member_achievements(
        self,
        *,
        guild_id: str,
        member_id: int,
    ) -> list[dict[str, Any]]:
        """멤버가 획득한 업적 목록을 최신순으로 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT ma.*, ad.code, ad.name, ad.description, ad.reward_score, ad.title_name
                FROM member_achievements AS ma
                JOIN achievement_definitions AS ad ON ad.id = ma.achievement_definition_id
                WHERE ma.guild_id = ? AND ma.member_id = ? AND ma.status = 'EARNED'
                ORDER BY ma.earned_at DESC, ma.id DESC;
                """,
                (guild_id, member_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def list_member_titles(
        self,
        *,
        guild_id: str,
        member_id: int,
    ) -> list[dict[str, Any]]:
        """멤버가 보유한 칭호와 장착 상태를 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT mt.*, td.title_name
                FROM member_titles AS mt
                JOIN title_definitions AS td ON td.id = mt.title_definition_id
                WHERE mt.guild_id = ? AND mt.member_id = ? AND td.is_active = 1
                ORDER BY mt.is_equipped DESC, td.title_name COLLATE NOCASE;
                """,
                (guild_id, member_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def set_achievement_role_mapping(
        self,
        *,
        guild_id: str,
        achievement_code: str,
        role_id: str,
        now: str,
    ) -> bool:
        """업적 코드와 Discord 역할 ID의 매핑을 저장한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                INSERT INTO achievement_role_mappings (
                    guild_id,
                    achievement_definition_id,
                    role_id,
                    created_at,
                    updated_at
                )
                SELECT ?, ad.id, ?, ?, ?
                FROM achievement_definitions AS ad
                WHERE ad.guild_id = ?
                  AND ad.code = ?
                  AND ad.is_active = 1
                ON CONFLICT(guild_id, achievement_definition_id) DO UPDATE SET
                    role_id = excluded.role_id,
                    updated_at = excluded.updated_at;
                """,
                (guild_id, role_id, now, now, guild_id, achievement_code),
            )
            await connection.commit()
            return cursor.rowcount > 0
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_achievement_role_mappings(
        self,
        *,
        guild_id: str,
    ) -> list[dict[str, Any]]:
        """서버의 업적-역할 매핑 목록을 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT
                    arm.id,
                    arm.role_id,
                    ad.code,
                    ad.name
                FROM achievement_role_mappings AS arm
                JOIN achievement_definitions AS ad
                    ON ad.id = arm.achievement_definition_id
                WHERE arm.guild_id = ?
                ORDER BY ad.code COLLATE NOCASE;
                """,
                (guild_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def equip_title(
        self,
        *,
        guild_id: str,
        member_id: int,
        title_name: str,
        now: str,
    ) -> bool:
        """멤버가 보유한 칭호 하나를 대표 칭호로 장착한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT mt.id
                FROM member_titles AS mt
                JOIN title_definitions AS td ON td.id = mt.title_definition_id
                WHERE mt.guild_id = ?
                  AND mt.member_id = ?
                  AND td.title_name = ?
                  AND td.is_active = 1;
                """,
                (guild_id, member_id, title_name),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                await connection.rollback()
                return False

            await connection.execute(
                """
                UPDATE member_titles
                SET is_equipped = 0,
                    equipped_at = NULL,
                    updated_at = ?
                WHERE guild_id = ? AND member_id = ?;
                """,
                (now, guild_id, member_id),
            )
            await connection.execute(
                """
                UPDATE member_titles
                SET is_equipped = 1,
                    equipped_at = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (now, now, row["id"]),
            )
            await connection.commit()
            return True
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def unequip_title(
        self,
        *,
        guild_id: str,
        member_id: int,
        now: str,
    ) -> None:
        """멤버의 대표 칭호 장착 상태를 해제한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                """
                UPDATE member_titles
                SET is_equipped = 0,
                    equipped_at = NULL,
                    updated_at = ?
                WHERE guild_id = ? AND member_id = ?;
                """,
                (now, guild_id, member_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_officer_settings(
        self,
        *,
        guild_id: str,
        now: str,
    ) -> dict[str, Any]:
        """서버의 간부 평가 설정을 조회하고 없으면 기본값으로 생성한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                """
                INSERT OR IGNORE INTO officer_review_settings (
                    guild_id,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?);
                """,
                (guild_id, now, now),
            )
            await connection.commit()
            cursor = await connection.execute(
                """
                SELECT *
                FROM officer_review_settings
                WHERE guild_id = ?;
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            return dict(row)
        finally:
            await connection.close()

    async def update_officer_settings(
        self,
        *,
        guild_id: str,
        values: dict[str, Any],
        now: str,
    ) -> dict[str, Any]:
        """간부 평가 설정 일부를 갱신하고 최신 설정을 반환한다."""

        await self.get_officer_settings(guild_id=guild_id, now=now)
        assignments = [f"{key} = ?" for key in values]
        params = [*values.values(), now, guild_id]

        connection = await self.database.connect()
        try:
            await connection.execute(
                f"""
                UPDATE officer_review_settings
                SET {", ".join(assignments)},
                    updated_at = ?
                WHERE guild_id = ?;
                """,
                tuple(params),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

        return await self.get_officer_settings(guild_id=guild_id, now=now)

    async def create_officer_review(
        self,
        *,
        guild_id: str,
        season_id: int | None,
        input_digest: str,
        created_by_discord_id: str | None,
        summary_json: str,
        result_json: str,
        now: str,
    ) -> int:
        """간부 인사 미리보기 결과를 저장하고 review ID를 반환한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                INSERT INTO officer_reviews (
                    guild_id,
                    season_id,
                    status,
                    input_digest,
                    created_by_discord_id,
                    created_at,
                    summary_json,
                    result_json,
                    updated_at
                )
                VALUES (?, ?, 'PREVIEW', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, input_digest) DO UPDATE SET
                    status = 'PREVIEW',
                    season_id = excluded.season_id,
                    summary_json = excluded.summary_json,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at;
                """,
                (
                    guild_id,
                    season_id,
                    input_digest,
                    created_by_discord_id,
                    now,
                    summary_json,
                    result_json,
                    now,
                ),
            )
            await connection.commit()
            if cursor.lastrowid:
                return cursor.lastrowid
            cursor = await connection.execute(
                """
                SELECT id
                FROM officer_reviews
                WHERE guild_id = ? AND input_digest = ?;
                """,
                (guild_id, input_digest),
            )
            row = await cursor.fetchone()
            await cursor.close()
            assert row is not None
            return row["id"]
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_officer_review(
        self,
        *,
        guild_id: str,
        review_id: int,
    ) -> dict[str, Any] | None:
        """저장된 간부 인사 미리보기 또는 실행 결과를 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM officer_reviews
                WHERE guild_id = ? AND id = ?;
                """,
                (guild_id, review_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else dict(row)
        finally:
            await connection.close()

    async def mark_officer_review_executed(
        self,
        *,
        guild_id: str,
        review_id: int,
        status: str,
        executed_by_discord_id: str,
        now: str,
    ) -> None:
        """간부 인사 리뷰를 실행 완료 또는 부분 실패 상태로 표시한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                """
                UPDATE officer_reviews
                SET status = ?,
                    executed_by_discord_id = ?,
                    executed_at = ?,
                    updated_at = ?
                WHERE guild_id = ? AND id = ?;
                """,
                (status, executed_by_discord_id, now, now, guild_id, review_id),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def create_role_change_log(
        self,
        *,
        guild_id: str,
        review_id: int | None,
        member_id: int | None,
        discord_id: str,
        action_type: str,
        from_role_id: str | None,
        to_role_id: str | None,
        status: str,
        reason: str,
        error_message: str | None,
        now: str,
    ) -> None:
        """Discord 역할 변경 시도 결과를 감사 로그 테이블에 기록한다."""

        connection = await self.database.connect()
        try:
            await connection.execute(
                """
                INSERT INTO officer_role_change_logs (
                    guild_id,
                    review_id,
                    member_id,
                    discord_id,
                    action_type,
                    from_role_id,
                    to_role_id,
                    status,
                    reason,
                    error_message,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    guild_id,
                    review_id,
                    member_id,
                    discord_id,
                    action_type,
                    from_role_id,
                    to_role_id,
                    status,
                    reason,
                    error_message,
                    now,
                    now,
                ),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_role_change_logs(
        self,
        *,
        guild_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """최근 간부 역할 변경 로그를 조회한다."""

        connection = await self.database.connect()
        try:
            cursor = await connection.execute(
                """
                SELECT *
                FROM officer_role_change_logs
                WHERE guild_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?;
                """,
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    @staticmethod
    def _achievement_dedup_key(
        member_id: int,
        achievement_definition_id: int,
        season_id: int | None,
    ) -> str:
        """업적 보상 점수 중복 지급을 막기 위한 dedup key를 만든다."""

        season_key = "global" if season_id is None else str(season_id)
        return f"achievement:{member_id}:{achievement_definition_id}:{season_key}"
