# ─────────────────────────────────────────
#  database.py  —  PostgreSQL 버전 (수정 불필요)
# ─────────────────────────────────────────
import os
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Railway PostgreSQL URL이 postgres:// 로 시작하면 postgresql:// 로 교체
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       BIGINT PRIMARY KEY,
                username      TEXT,
                full_name     TEXT,
                points        INTEGER DEFAULT 0,
                referred_by   BIGINT,
                referral_done INTEGER DEFAULT 0,
                joined_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          SERIAL PRIMARY KEY,
                inviter_id  BIGINT NOT NULL,
                invitee_id  BIGINT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(inviter_id, invitee_id)
            )
        """)


async def get_user(user_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", user_id
        )
        return dict(row) if row else None


async def register_user(user_id: int, username: str, full_name: str) -> dict:
    from config import POINTS_JOIN
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, username, full_name, points)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (user_id) DO NOTHING""",
            user_id, username, full_name, POINTS_JOIN,
        )
    return await get_user(user_id)


async def get_user_by_username(username: str) -> dict | None:
    clean = username.lstrip("@").lower()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE LOWER(username) = $1", clean
        )
        return dict(row) if row else None


async def set_referral_done(invitee_id: int, inviter_id: int):
    from config import POINTS_REFER, POINTS_INVITED
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET referred_by = $1, referral_done = 1 WHERE user_id = $2",
                inviter_id, invitee_id,
            )
            await conn.execute(
                """INSERT INTO referrals (inviter_id, invitee_id)
                   VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                inviter_id, invitee_id,
            )
            await conn.execute(
                "UPDATE users SET points = points + $1 WHERE user_id = $2",
                POINTS_REFER, invitee_id,
            )
            await conn.execute(
                "UPDATE users SET points = points + $1 WHERE user_id = $2",
                POINTS_INVITED, inviter_id,
            )


async def get_referral_count(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = $1", user_id
        )
        return row["count"] if row else 0


async def get_total_participants() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM users")
        return row["count"] if row else 0


async def reset_points(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET points = 0 WHERE user_id = $1", user_id
        )


async def get_leaderboard(limit: int = 10) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.user_id, u.username, u.full_name, u.points,
                      COUNT(r.id) AS invite_count
               FROM users u
               LEFT JOIN referrals r ON r.inviter_id = u.user_id
               GROUP BY u.user_id
               ORDER BY u.points DESC
               LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]
