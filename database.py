# ─────────────────────────────────────────
#  database.py  —  수정 불필요
# ─────────────────────────────────────────
import aiosqlite

DB_PATH = "referral.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                full_name     TEXT,
                points        INTEGER DEFAULT 0,
                referred_by   INTEGER,
                referral_done INTEGER DEFAULT 0,
                joined_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id  INTEGER NOT NULL,
                invitee_id  INTEGER NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(inviter_id, invitee_id)
            )
        """)
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def register_user(user_id: int, username: str, full_name: str) -> dict:
    from config import POINTS_JOIN
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, POINTS_JOIN),
        )
        await db.commit()
    return await get_user(user_id)


async def get_user_by_username(username: str) -> dict | None:
    clean = username.lstrip("@").lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE LOWER(username) = ?", (clean,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_referral_done(invitee_id: int, inviter_id: int):
    from config import POINTS_REFER, POINTS_INVITED
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET referred_by = ?, referral_done = 1 WHERE user_id = ?",
            (inviter_id, invitee_id),
        )
        await db.execute(
            "INSERT OR IGNORE INTO referrals (inviter_id, invitee_id) VALUES (?, ?)",
            (inviter_id, invitee_id),
        )
        await db.execute(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            (POINTS_REFER, invitee_id),
        )
        await db.execute(
            "UPDATE users SET points = points + ? WHERE user_id = ?",
            (POINTS_INVITED, inviter_id),
        )
        await db.commit()


async def get_referral_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_total_participants() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_leaderboard(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, username, full_name, points,
                      (SELECT COUNT(*) FROM referrals r WHERE r.inviter_id = users.user_id) AS invite_count
               FROM users ORDER BY points DESC LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
