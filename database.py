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
        # [추가] 보너스 중복 수령 방지 테이블
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_claims (
                user_id   BIGINT NOT NULL,
                milestone INTEGER NOT NULL,
                PRIMARY KEY (user_id, milestone)
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
    """본인 포인트 0으로 초기화 + 추천인 포인트 차감"""
    from config import POINTS_REFER, POINTS_INVITED
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 나를 추천한 사람(추천인)에게서 포인트 차감
            referred_by = await conn.fetchval(
                "SELECT referred_by FROM users WHERE user_id = $1", user_id
            )
            if referred_by:
                await conn.execute(
                    "UPDATE users SET points = GREATEST(0, points - $1) WHERE user_id = $2",
                    POINTS_INVITED, referred_by,
                )

            # 내가 초대한 사람들의 추천 포인트도 차감 (내가 추천인인 경우)
            await conn.execute(
                """UPDATE users SET points = GREATEST(0, points - $1)
                   WHERE user_id IN (
                       SELECT invitee_id FROM referrals WHERE inviter_id = $2
                   )""",
                POINTS_REFER, user_id,
            )

            # 본인 포인트 0으로 초기화
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

# ... (기존 코드 맨 마지막 줄 아래에 추가)

async def process_loyalty_bonus(user_id: int, username: str):
    """우대 대상자인 경우 마일스톤 체크 및 pt 지급"""
    import config
    if not username or username.lower() not in config.LOYALTY_USERS:
        return 0

    pool = await get_pool()
    total_awarded = 0
    async with pool.acquire() as conn:
        # 현재 초대 인원 조회
        count = await conn.fetchval("SELECT COUNT(*) FROM referrals WHERE inviter_id = $1", user_id)
        
        for milestone, bonus in config.LOYALTY_BONUS.items():
            if count >= milestone:
                exists = await conn.fetchval(
                    "SELECT 1 FROM loyalty_claims WHERE user_id=$1 AND milestone=$2", 
                    user_id, milestone
                )
                if not exists:
                    async with conn.transaction():
                        await conn.execute("INSERT INTO loyalty_claims VALUES ($1, $2)", user_id, milestone)
                        await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", bonus, user_id)
                        total_awarded += bonus
    return total_awarded
