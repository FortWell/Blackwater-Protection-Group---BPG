from __future__ import annotations

from pathlib import Path

import aiosqlite


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS infractions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                staff_id INTEGER NOT NULL,
                punishment TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                ai_flagged INTEGER NOT NULL DEFAULT 0,
                max_ai_score REAL NOT NULL DEFAULT 0,
                answers_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embed_message_buttons (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                buttons_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                strike_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT,
                strike_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES application_sessions(id)
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS application_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                applicant_id INTEGER NOT NULL,
                applicant_tag TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_bans (
                user_id INTEGER PRIMARY KEY,
                user_tag TEXT NOT NULL,
                reason TEXT NOT NULL,
                notes TEXT NOT NULL,
                banned_by_id INTEGER NOT NULL,
                banned_by_tag TEXT NOT NULL,
                banned_guild_id INTEGER NOT NULL,
                banned_guild_name TEXT NOT NULL,
                banned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER NOT NULL DEFAULT 1,
                unbanned_at TEXT,
                unbanned_by_id INTEGER,
                unbanned_by_tag TEXT,
                unban_reason TEXT,
                unban_notes TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_role_associations (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, role_id)
            )
            """
        )
        await self.conn.commit()

    async def execute(self, query: str, params: tuple = ()) -> None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(query, params)
        await self.conn.commit()

    async def execute_insert(self, query: str, params: tuple = ()) -> int:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        cursor = await self.conn.execute(query, params)
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def fetch_value(self, query: str, params: tuple = ()) -> str | None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        first = row[0]
        if first is None:
            return None
        return str(first)

    async def fetch_rows(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        async with self.conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return rows

    async def fetch_row(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        async with self.conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return row

    async def get_setting(self, key: str, default: str = "") -> str:
        value = await self.fetch_value(
            "SELECT value FROM bot_settings WHERE key = ?",
            (key,),
        )
        if value is None:
            return default
        return value

    async def set_setting(self, key: str, value: str) -> None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(
            """
            INSERT INTO bot_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await self.conn.commit()

    async def upsert_global_ban(
        self,
        *,
        user_id: int,
        user_tag: str,
        reason: str,
        notes: str,
        banned_by_id: int,
        banned_by_tag: str,
        banned_guild_id: int,
        banned_guild_name: str,
    ) -> None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(
            """
            INSERT INTO global_bans (
                user_id,
                user_tag,
                reason,
                notes,
                banned_by_id,
                banned_by_tag,
                banned_guild_id,
                banned_guild_name,
                banned_at,
                is_active,
                unbanned_at,
                unbanned_by_id,
                unbanned_by_tag,
                unban_reason,
                unban_notes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                user_tag = excluded.user_tag,
                reason = excluded.reason,
                notes = excluded.notes,
                banned_by_id = excluded.banned_by_id,
                banned_by_tag = excluded.banned_by_tag,
                banned_guild_id = excluded.banned_guild_id,
                banned_guild_name = excluded.banned_guild_name,
                banned_at = CURRENT_TIMESTAMP,
                is_active = 1,
                unbanned_at = NULL,
                unbanned_by_id = NULL,
                unbanned_by_tag = NULL,
                unban_reason = NULL,
                unban_notes = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                user_tag,
                reason,
                notes,
                banned_by_id,
                banned_by_tag,
                banned_guild_id,
                banned_guild_name,
            ),
        )
        await self.conn.commit()

    async def set_global_unban(
        self,
        *,
        user_id: int,
        unbanned_by_id: int,
        unbanned_by_tag: str,
        reason: str,
        notes: str,
    ) -> bool:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(
            """
            UPDATE global_bans
            SET is_active = 0,
                unbanned_at = CURRENT_TIMESTAMP,
                unbanned_by_id = ?,
                unbanned_by_tag = ?,
                unban_reason = ?,
                unban_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND is_active = 1
            """,
            (unbanned_by_id, unbanned_by_tag, reason, notes, user_id),
        )
        await self.conn.commit()
        return True

    async def fetch_global_ban(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetch_row(
            """
            SELECT *
            FROM global_bans
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )

    async def fetch_active_global_bans(self) -> list[aiosqlite.Row]:
        return await self.fetch_rows(
            """
            SELECT *
            FROM global_bans
            WHERE is_active = 1
            ORDER BY banned_at DESC, user_tag COLLATE NOCASE ASC
            """
        )

    async def upsert_embed_message_buttons(
        self,
        *,
        message_id: int,
        guild_id: int,
        channel_id: int,
        buttons_json: str,
    ) -> None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(
            """
            INSERT INTO embed_message_buttons (message_id, guild_id, channel_id, buttons_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                channel_id = excluded.channel_id,
                buttons_json = excluded.buttons_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (message_id, guild_id, channel_id, buttons_json),
        )
        await self.conn.commit()

    async def delete_embed_message_buttons(self, message_id: int) -> None:
        if self.conn is None:
            raise RuntimeError("Database not initialized.")
        await self.conn.execute(
            "DELETE FROM embed_message_buttons WHERE message_id = ?",
            (message_id,),
        )
        await self.conn.commit()

    async def fetch_embed_message_button_rows(self) -> list[aiosqlite.Row]:
        return await self.fetch_rows(
            """
            SELECT message_id, guild_id, channel_id, buttons_json
            FROM embed_message_buttons
            ORDER BY message_id
            """
        )

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
