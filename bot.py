# ─────────────────────────────────────────
#  bot.py  —  수정 불필요
# ─────────────────────────────────────────
import logging
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import BadRequest, Forbidden

import config
import database as db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WAITING_REFERRER = 1


# ────────────────────────────────────────
#  채널 가입 확인
# ────────────────────────────────────────
async def check_channels(user_id: int, bot) -> list[str]:
    """미가입 채널 이름 목록 반환. 빈 리스트면 전부 가입된 것."""
    not_joined = []
    for channel, (label, _) in zip(config.REQUIRED_CHANNELS, config.CHANNEL_INFO):
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(label)
        except (BadRequest, Forbidden):
            not_joined.append(label)
    return not_joined


def join_buttons() -> InlineKeyboardMarkup:
    """채널 입장 버튼 + 확인 버튼"""
    buttons = [
        [InlineKeyboardButton(f"📢 {label}", url=url)]
        for label, url in config.CHANNEL_INFO
    ]
    buttons.append(
        [InlineKeyboardButton("✅ 가입 완료 — 확인하기", callback_data="verify_join")]
    )
    return InlineKeyboardMarkup(buttons)


# ────────────────────────────────────────
#  /start
# ────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user

    # 이미 등록된 유저
    existing = await db.get_user(user.id)
    if existing:
        invite_count = await db.get_referral_count(user.id)
        status = "완료 ✅" if existing["referral_done"] else "미입력"
        await update.message.reply_text(
            f"이미 참여 중입니다, {existing['full_name']}님!\n\n"
            f"🏆 내 포인트: {existing['points']}pt\n"
            f"👥 초대한 친구: {invite_count}명\n"
            f"📎 추천인 입력: {status}\n\n"
            "친구에게 내 @username을 알려 포인트를 함께 쌓으세요!"
        )
        return ConversationHandler.END

    # 채널 가입 확인
    not_joined = await check_channels(user.id, context.bot)
    if not_joined:
        not_joined_text = "\n".join(f"• {ch}" for ch in not_joined)
        await update.message.reply_text(
            f"{config.EVENT_TITLE}\n\n"
            "⚠️ 아래 채널에 아직 입장하지 않으셨어요!\n\n"
            f"{not_joined_text}\n\n"
            "📌 채널/그룹 입장 후 입장 인증(더하기 문제)을 완료한 뒤\n"
            "아래 [✅ 가입 완료 — 확인하기] 버튼을 눌러주세요!",
            reply_markup=join_buttons(),
        )
        return ConversationHandler.END

    # 채널 가입 확인됨 → 등록 진행
    return await _register_and_greet(user, context)


# ────────────────────────────────────────
#  "가입 완료 확인" 버튼 콜백
# ────────────────────────────────────────
async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user  = query.from_user
    await query.answer()

    # 이미 등록된 유저
    existing = await db.get_user(user.id)
    if existing:
        invite_count = await db.get_referral_count(user.id)
        await query.edit_message_text(
            f"이미 참여 중입니다, {existing['full_name']}님!\n\n"
            f"🏆 내 포인트: {existing['points']}pt\n"
            f"👥 초대한 친구: {invite_count}명"
        )
        return ConversationHandler.END

    # 채널 재확인
    not_joined = await check_channels(user.id, context.bot)
    if not_joined:
        not_joined_text = "\n".join(f"• {ch}" for ch in not_joined)
        await query.edit_message_text(
            "❌ 아직 입장하지 않은 채널이 있어요!\n\n"
            f"{not_joined_text}\n\n"
            "📌 채널/그룹 입장 후 입장 인증(더하기 문제)을 완료한 뒤\n"
            "다시 버튼을 눌러주세요!",
            reply_markup=join_buttons(),
        )
        return ConversationHandler.END

    # 모두 가입 확인 → 등록 진행
    await query.edit_message_text("✅ 채널 가입 확인됐습니다! 잠시만요...")
    return await _register_and_greet(user, context)


# ────────────────────────────────────────
#  등록 + 환영 메시지 공통 함수
# ────────────────────────────────────────
async def _register_and_greet(user, context) -> int:
    username  = user.username or ""
    full_name = user.full_name or str(user.id)
    await db.register_user(user.id, username, full_name)

    # 메시지 1 — 이벤트 소개 + 채널 링크
    channel_lines = "\n".join(
        f"{i+1}️⃣ {label}: {url}"
        for i, (label, url) in enumerate(config.CHANNEL_INFO)
    )
    await context.bot.send_message(
        user.id,
        f"{config.EVENT_TITLE}\n"
        f"📅 기간: {config.EVENT_PERIOD}\n"
        f"💰 보상: {config.EVENT_REWARD}\n\n"
        f"✅ 아래 {len(config.CHANNEL_INFO)}개 채널에 모두 입장해 주세요!\n"
        f"{channel_lines}\n\n"
        f"{len(config.CHANNEL_INFO)}개 채널 입장 후 아래 절차를 따라주세요!"
    )

    # 메시지 2 — 추천 방식 안내
    await context.bot.send_message(
        user.id,
        f"{config.EVENT_TITLE}\n\n"
        f"친구를 초대하면 나와 친구 모두 {config.POINTS_INVITED}포인트씩 지급됩니다.\n"
        "포인트는 이벤트 종료 후 리워드로 환산됩니다.\n\n"
        "👇 나를 이곳에 초대한 사람의 @유저네임을 입력해주세요!"
    )

    # 메시지 3 — 환영 + 포인트 지급
    await context.bot.send_message(
        user.id,
        f"✅ 환영합니다, {full_name}님!\n"
        f"🎉 기본 +{config.POINTS_JOIN} 포인트가 지급됐습니다!\n\n"
        "나를 이곳에 초대한 사람의 텔레그램 @유저네임을 입력하면\n"
        f"👉 나에게 +{config.POINTS_REFER}포인트 추가 지급\n"
        f"👉 초대한 친구에게도 +{config.POINTS_INVITED}포인트 지급\n\n"
        "(예: @username)\n"
        "없으면 /skip 입력"
    )
    return WAITING_REFERRER


# ────────────────────────────────────────
#  추천인 입력
# ────────────────────────────────────────
async def receive_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text.strip()

    # 자기 자신 방지
    clean = text.lstrip("@").lower()
    if user.username and clean == user.username.lower():
        await update.message.reply_text(
            "❌ 자기 자신을 추천인으로 입력할 수 없습니다.\n"
            "다른 @유저네임을 입력하거나 /skip 을 입력하세요."
        )
        return WAITING_REFERRER

    # 추천인 조회
    referrer = await db.get_user_by_username(text)
    if not referrer:
        await update.message.reply_text(
            f"❌ @{clean} 는 이벤트에 참여하지 않은 유저입니다.\n"
            "올바른 @유저네임을 입력하거나 /skip 을 입력하세요."
        )
        return WAITING_REFERRER

    # 포인트 지급 + 관계 저장
    await db.set_referral_done(user.id, referrer["user_id"])

    # 추천인에게 DM 알림
    try:
        invite_count = await db.get_referral_count(referrer["user_id"])
        await context.bot.send_message(
            referrer["user_id"],
            f"🎉 @{user.username or user.full_name} 님이 당신의 초대로 참여했습니다!\n"
            f"👉 +{config.POINTS_INVITED}pt 지급!\n"
            f"(현재 누적: {referrer['points'] + config.POINTS_INVITED}pt)\n"
            f"📊 총 초대: {invite_count}명",
        )
    except Exception:
        pass

    me = await db.get_user(user.id)
    await update.message.reply_text(
        f"✅ 추천인 @{referrer['username']} 처리 완료!\n\n"
        f"🎉 나에게 +{config.POINTS_REFER}pt 추가 지급!\n"
        f"🎉 @{referrer['username']}에게도 +{config.POINTS_INVITED}pt 지급!\n\n"
        f"🏆 내 현재 포인트: {me['points']}pt\n\n"
        "친구에게 내 @username을 공유해서 함께 포인트를 쌓으세요!"
    )
    return ConversationHandler.END


async def skip_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user     = update.effective_user
    existing = await db.get_user(user.id)

    if existing and not existing["referral_done"]:
        async with aiosqlite.connect(db.DB_PATH) as conn:
            await conn.execute(
                "UPDATE users SET referral_done = 1 WHERE user_id = ?", (user.id,)
            )
            await conn.commit()

    pts = existing["points"] if existing else config.POINTS_JOIN
    await update.message.reply_text(
        "⏭ 추천인 입력을 건너뛰었습니다.\n\n"
        f"🏆 내 포인트: {pts}pt\n\n"
        "친구에게 내 @username을 알려주면 둘 다 포인트를 받을 수 있어요 😊"
    )
    return ConversationHandler.END


# ────────────────────────────────────────
#  /points
# ────────────────────────────────────────
async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    existing = await db.get_user(user.id)
    if not existing:
        await update.message.reply_text("아직 참여하지 않으셨어요. /start 로 시작하세요!")
        return

    invite_count = await db.get_referral_count(user.id)
    total        = await db.get_total_participants()
    await update.message.reply_text(
        f"📊 내 이벤트 현황\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 이름: {existing['full_name']}\n"
        f"🏆 포인트: {existing['points']}pt\n"
        f"👥 초대한 친구: {invite_count}명\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📈 전체 참여자: {total}명"
    )


# ────────────────────────────────────────
#  /ranking
# ────────────────────────────────────────
async def ranking_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    board = await db.get_leaderboard(10)
    if not board:
        await update.message.reply_text("아직 참여자가 없습니다.")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines  = ["🏆 포인트 TOP 10\n━━━━━━━━━━━━━━━"]
    for i, row in enumerate(board):
        name = f"@{row['username']}" if row["username"] else row["full_name"]
        lines.append(
            f"{medals[i]} {i+1}위  {name}  "
            f"{row['points']}pt  (초대 {row['invite_count']}명)"
        )
    await update.message.reply_text("\n".join(lines))


# ────────────────────────────────────────
#  /help
# ────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 사용 가능한 명령어\n\n"
        "/start   — 이벤트 참여 시작\n"
        "/points  — 내 포인트 확인\n"
        "/ranking — 포인트 랭킹 TOP 10\n"
        "/skip    — 추천인 입력 건너뛰기\n"
        "/help    — 이 도움말"
    )


# ────────────────────────────────────────
#  메인
# ────────────────────────────────────────
async def post_init(application: Application):
    await db.init_db()
    logger.info("DB 초기화 완료")


def main():
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(verify_join_callback, pattern="^verify_join$"),
        ],
        states={
            WAITING_REFERRER: [
                CommandHandler("skip", skip_referrer),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_referrer),
            ]
        },
        fallbacks=[CommandHandler("skip", skip_referrer)],
        per_user=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(verify_join_callback, pattern="^verify_join$"))
    app.add_handler(CommandHandler("points",  points_cmd))
    app.add_handler(CommandHandler("ranking", ranking_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))

    logger.info("봇 시작!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
