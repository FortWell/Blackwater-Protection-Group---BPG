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

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
