# ─────────────────────────────────────────
#  database.py  —  PostgreSQL 버전
# ─────────────────────────────────────────
import os
import logging
import asyncpg

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Railway PostgreSQL URL이 postgres:// 로 시작하면 postgresql:// 로 교체
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 기존 테이블 (신규 설치 시 새 컬럼 포함)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id                BIGINT PRIMARY KEY,
                username               TEXT,
                full_name              TEXT,
                points                 INTEGER DEFAULT 0,
                referred_by            BIGINT,
                referral_done          INTEGER DEFAULT 0,
                claimed_referrer_bonus BOOLEAN DEFAULT FALSE,
                joined_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_claims (
                user_id   BIGINT NOT NULL,
                milestone INTEGER NOT NULL,
                PRIMARY KEY (user_id, milestone)
            )
        """)

        # ────────────────────────────────────────
        #  마이그레이션 (트랜잭션으로 묶기)
        #  ALTER + 백필이 함께 성공하거나 함께 롤백
        # ────────────────────────────────────────
        async with conn.transaction():
            await conn.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS claimed_referrer_bonus BOOLEAN DEFAULT FALSE
            """)
            # 이미 진짜 추천인을 입력한 사람은 보너스 받은 것으로 간주
            # 스킵한 사람(referred_by IS NULL)은 FALSE 유지 → /referral 로 구제 가능
            result = await conn.execute("""
                UPDATE users SET claimed_referrer_bonus = TRUE
                WHERE referred_by IS NOT NULL AND claimed_referrer_bonus = FALSE
            """)
            logger.info(f"마이그레이션 완료: {result}")


async def get_user(user_id: int, current_username: str = None, current_full_name: str = None) -> dict | None:
    """유저 조회. current_username/full_name이 주어지면 자동 sync."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if current_username is not None or current_full_name is not None:
            # 현재 텔레그램 정보로 DB 갱신 (변경된 경우만)
            await conn.execute(
                """UPDATE users
                   SET username = COALESCE($1, username),
                       full_name = COALESCE($2, full_name)
                   WHERE user_id = $3
                     AND ( (username IS DISTINCT FROM $1 AND $1 IS NOT NULL)
                        OR (full_name IS DISTINCT FROM $2 AND $2 IS NOT NULL) )""",
                current_username, current_full_name, user_id,
            )
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
    """실제 추천인 입력 처리 (양쪽 모두에게 포인트 지급)"""
    from config import POINTS_REFER, POINTS_INVITED
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE users
                   SET referred_by = $1,
                       referral_done = 1,
                       claimed_referrer_bonus = TRUE
                   WHERE user_id = $2""",
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


async def set_official_referrer(user_id: int) -> int:
    """공식 추천인(@midnight_kor) 적용. 본인에게만 POINTS_REFER 지급.
    이미 보너스 받은 경우 0 반환."""
    from config import POINTS_REFER
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            already = await conn.fetchval(
                "SELECT claimed_referrer_bonus FROM users WHERE user_id = $1",
                user_id,
            )
            # already가 None이면 유저 미등록 (정상 흐름상 안 일어나지만 방어)
            if already is None or already:
                return 0
            await conn.execute(
                """UPDATE users
                   SET points = points + $1,
                       referral_done = 1,
                       claimed_referrer_bonus = TRUE
                   WHERE user_id = $2""",
                POINTS_REFER, user_id,
            )
    return POINTS_REFER


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


async def process_loyalty_bonus(user_id: int, username: str) -> int:
    """우대 대상자인 경우 마일스톤 체크 및 pt 지급 (동시성 안전)"""
    import config
    if not username or username.lower() not in config.LOYALTY_USERS:
        return 0

    pool = await get_pool()
    total_awarded = 0
    async with pool.acquire() as conn:
        # 현재 초대 인원 조회
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id = $1", user_id
        )

        for milestone, bonus in config.LOYALTY_BONUS.items():
            if count >= milestone:
                # ON CONFLICT DO NOTHING으로 중복 INSERT 방지
                # res가 "INSERT 0 1" 이면 실제로 새로 들어간 것
                res = await conn.execute(
                    """INSERT INTO loyalty_claims (user_id, milestone)
                       VALUES ($1, $2) ON CONFLICT DO NOTHING""",
                    user_id, milestone,
                )
                if res.split()[-1] == "1":
                    # 실제 INSERT 된 경우에만 포인트 지급
                    await conn.execute(
                        "UPDATE users SET points = points + $1 WHERE user_id = $2",
                        bonus, user_id,
                    )
                    total_awarded += bonus
    return total_awarded
