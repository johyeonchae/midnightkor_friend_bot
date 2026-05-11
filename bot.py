# ─────────────────────────────────────────
#  bot.py  —  최종 수정본
# ─────────────────────────────────────────
import logging
import os  # 추가
import csv # 추가
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
#  [추가] 우대 대상자 체크 로직
# ────────────────────────────────────────
def is_loyalty_user(username: str) -> bool:
    if not username: return False
    return username.lstrip("@").lower() in config.LOYALTY_USERS

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
            f"이미 참여 중이세요, {existing['full_name']}님 🌙\n\n"
            f"🏆 보유 포인트: {existing['points']}pt\n"
            f"👥 초대한 친구: {invite_count}명\n"
            f"📎 추천인 입력: {status}\n\n"
            "내 @username을 친구에게 알려 함께 포인트를 쌓아보세요!"
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

    return await _register_and_greet(user, context)


# ────────────────────────────────────────
#  "가입 완료 확인" 버튼 콜백
# ────────────────────────────────────────
async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user  = query.from_user
    await query.answer()

    existing = await db.get_user(user.id)
    if existing:
        invite_count = await db.get_referral_count(user.id)
        await query.edit_message_text(
            f"이미 참여 중이세요, {existing['full_name']}님 🌙\n\n"
            f"🏆 보유 포인트: {existing['points']}pt\n"
            f"👥 초대한 친구: {invite_count}명"
        )
        return ConversationHandler.END

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

    await query.edit_message_text("✅ 채널 가입 확인됐습니다! 잠시만요...")
    return await _register_and_greet(user, context)


# ────────────────────────────────────────
#  등록 + 환영 메시지
# ────────────────────────────────────────
async def _register_and_greet(user, context) -> int:
    username  = user.username or ""
    full_name = user.full_name or str(user.id)
    await db.register_user(user.id, username, full_name)

    # ────────────────────────────────────────
    #  [수정] 가입 시 우대 대상자 전용 환영 문구 추가
    # ────────────────────────────────────────
    loyalty_text = ""
    if is_loyalty_user(username):
        loyalty_text = (
            "🌟 **[특별 우대 대상자 선정 안내]**\n"
            "이전 이벤트 참여 기록이 확인되어 특별 우대 대상으로 등록되셨습니다!\n"
            "친구 초대 달성 시마다 추가 보너스 pt가 자동 지급됩니다. (최대 1,900pt 추가 보상)\n"
            "자세한 내용은 /points 를 입력하여 확인해 보세요!\n"
            "━━━━━━━━━━━━━━━\n\n"
        )

    # 메시지 1 — 이벤트 소개 + 환영
    await context.bot.send_message(
        user.id,
        f"{config.EVENT_TITLE}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{loyalty_text}"  # 우대 대상자일 경우 여기에 특별 문구가 삽입됩니다.
        f"📅 기간: {config.EVENT_PERIOD}\n"
        f"🎁 리워드: {config.EVENT_REWARD}\n\n"
        f"🎉 {full_name}님, 환영합니다!\n"
        f"참여 보상으로 {config.POINTS_JOIN}pt가 지급됐어요.\n\n"
        f"친구를 초대하면 나와 친구 모두 {config.POINTS_INVITED}pt씩 추가 지급됩니다.\n"
        "포인트는 미드나잇 코리아 캠페인에 누적되며 종료 후 리워드로 환산됩니다.\n\n"
        "⚠️ 이벤트 종료 전까지 채널에 남아 계셔야 포인트가 유지됩니다. 채널을 나가면 본인과 추천인 포인트가 모두 차감됩니다!",
        parse_mode="Markdown" # 볼드체(**) 적용을 위해 추가
    )

    # 메시지 2 — 추천인 입력 요청 (기존과 동일)
    await context.bot.send_message(
        user.id,
        f"👥 나를 초대한 분의 @유저네임을 입력하면\n"
        f"👉 나에게 +{config.POINTS_REFER}pt 추가\n"
        f"👉 초대한 분에게도 +{config.POINTS_INVITED}pt 지급\n\n"
        "(예: @username)\n"
        "초대한 분이 없다면 /skip"
    )
    return WAITING_REFERRER


# ────────────────────────────────────────
#  추천인 입력
# ────────────────────────────────────────
async def receive_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text.strip()

    clean = text.lstrip("@").lower()

    # 자기 자신 방지
    if user.username and clean == user.username.lower():
        await update.message.reply_text(
            "❌ 자기 자신을 추천인으로 입력할 수 없어요.\n"
            "다른 @유저네임을 입력하거나 /skip 을 입력하세요."
        )
        return WAITING_REFERRER

    # 추천인 조회
    referrer = await db.get_user_by_username(text)
    if not referrer:
        await update.message.reply_text(
            f"❌ @{clean} 님은 아직 이벤트에 참여하지 않으셨어요.\n"
            "올바른 @유저네임을 입력하거나 /skip 을 입력하세요."
        )
        return WAITING_REFERRER

    # 포인트 지급 + 관계 저장
    await db.set_referral_done(user.id, referrer["user_id"])

    # [추가] 추천인(Inviter)이 우대 대상자인 경우 보너스 체크
    bonus_pt = await db.process_loyalty_bonus(referrer["user_id"], referrer["username"])

    # 추천인에게 DM 알림
    try:
        invite_count = await db.get_referral_count(referrer["user_id"])
        msg = (
            f"🎉 @{user.username or user.full_name} 님이 회원님의 초대로 참여했습니다!\n"
            f"👉 +{config.POINTS_INVITED}pt 적립!\n"
        )
        if bonus_pt > 0:
            msg += f"🎁 [우대 혜택] 초대 목표 달성 보너스 {bonus_pt}pt 추가 적립!\n"
            
        msg += f"현재 누적 포인트: {referrer['points'] + config.POINTS_INVITED + bonus_pt}pt\n"
        msg += f"📊 총 초대: {invite_count}명"

        await context.bot.send_message(referrer["user_id"], msg)
    except Exception:
        pass

    me = await db.get_user(user.id)
    await update.message.reply_text(
        f"✅ 추천인 입력 완료!\n\n"
        f"🎉 나에게 +{config.POINTS_REFER}pt 추가 적립!\n"
        f"🎉 @{referrer['username']}님께도 +{config.POINTS_INVITED}pt 지급!\n\n"
        f"🏆 현재 내 포인트: {me['points']}pt\n\n"
        "내 @username을 친구에게 공유해서 함께 포인트를 쌓아보세요 🌙"
    )
    return ConversationHandler.END


# ────────────────────────────────────────
#  /skip
# ────────────────────────────────────────
async def skip_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user     = update.effective_user
    existing = await db.get_user(user.id)

    if existing and not existing["referral_done"]:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET referral_done = 1 WHERE user_id = $1", user.id
            )

    pts = existing["points"] if existing else config.POINTS_JOIN
    await update.message.reply_text(
        "⏭ 추천인 입력을 건너뛰었습니다.\n\n"
        f"🏆 현재 내 포인트: {pts}pt\n\n"
        "나중에라도 친구에게 내 @username을 알려주면\n"
        "둘 다 포인트를 받을 수 있어요 😊"
    )
    return ConversationHandler.END


# ────────────────────────────────────────
#  /points — 내 포인트 확인 (채널 재확인 포함)
# ────────────────────────────────────────
async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    existing = await db.get_user(user.id)
    if not existing:
        await update.message.reply_text(
            "아직 참여하지 않으셨어요.\n/start 로 이벤트를 시작해보세요!"
        )
        return

    # 채널 재확인 — 추방/탈퇴 시 포인트 초기화
    not_joined = await check_channels(user.id, context.bot)
    if not_joined:
        await db.reset_points(user.id)
        not_joined_text = "\n".join(f"• {ch}" for ch in not_joined)
        await update.message.reply_text(
            "⚠️ 채널 탈퇴/추방이 확인됐습니다.\n\n"
            f"{not_joined_text}\n\n"
            "❌ 포인트가 0으로 초기화됐습니다.\n"
            "채널 재입장 후 /start 를 다시 입력해주세요."
        )
        return

    invite_count = await db.get_referral_count(user.id)
    total        = await db.get_total_participants()
    existing     = await db.get_user(user.id)  # 초기화 후 재조회

    # ────────────────────────────────────────
    #  [수정] 우대 대상자 전용 정보 추가
    # ────────────────────────────────────────
    loyalty_info = ""
    if is_loyalty_user(user.username):
        loyalty_info = (
            "🌟 **특별 우대 대상자**\n"
            "초대 마일스톤 보너스 적용 중:\n"
            "• 1명(+100pt) / 3명(+300pt)\n"
            "• 5명(+500pt) / 10명(+1000pt)\n"
            "━━━━━━━━━━━━━━━\n"
        )
        
    await update.message.reply_text(
        f"📊 내 이벤트 현황\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 닉네임: {existing['full_name']}\n"
        f"🏆 보유 포인트: {existing['points']}pt\n"
        f"👥 초대한 친구: {invite_count}명\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📈 전체 참여자: {total}명"
    )


# ────────────────────────────────────────
#  /ranking — TOP 10 리더보드
# ────────────────────────────────────────
async def ranking_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    board = await db.get_leaderboard(10)
    if not board:
        await update.message.reply_text("아직 참여자가 없습니다.")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines  = ["🏆 포인트 랭킹 TOP 10\n━━━━━━━━━━━━━━━"]
    for i, row in enumerate(board):
        lines.append(
            f"{medals[i]} {i+1}위  "
            f"{row['points']}pt  (초대 {row['invite_count']}명)"
        )
    await update.message.reply_text("\n".join(lines))


# ────────────────────────────────────────
#  /invite — 내 초대 링크 안내
# ────────────────────────────────────────
async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    existing = await db.get_user(user.id)
    if not existing:
        await update.message.reply_text(
            "아직 참여하지 않으셨어요.\n/start 로 이벤트를 시작해보세요!"
        )
        return

    if not user.username:
        await update.message.reply_text(
            "⚠️ 텔레그램 username이 설정되어 있지 않아요.\n"
            "텔레그램 설정 → 사용자 이름 설정 후 다시 시도해주세요."
        )
        return

    invite_count = await db.get_referral_count(user.id)
    
    # [추가] 우대 대상자 여부 확인 후 문구 구성
    msg = f"📤 내 초대 정보\n━━━━━━━━━━━━━━━\n"
    msg += f"내 @username: @{user.username}\n"
    
    if is_loyalty_user(user.username):
        msg += "\n🌟 **[특별 우대 대상자 혜택]**\n"
        msg += "초대 달성 시마다 pt가 자동 추가됩니다!\n"
        msg += "• 1명(+100pt) / 3명(+300pt) / 5명(+500pt) / 10명(+1000pt)\n"
        
    msg += f"\n👥 지금까지 초대한 친구: {invite_count}명\n"
    msg += f"🏆 보유 포인트: {existing['points']}pt\n"
    msg += "━━━━━━━━━━━━━━━\n\n"
    msg += "친구에게 아래 메시지를 공유하세요!\n\n"
    msg += "👇👇👇\n"
    msg += f"🌙 Midnight Network 이벤트 참여하고 포인트 받자!\n"
    msg += f"@midnightkor_friend_bot 에서 /start 입력 후\n"
    msg += f"추천인 @{user.username} 입력하면 둘 다 {config.POINTS_INVITED}pt 지급!"

    await update.message.reply_text(msg, parse_mode="Markdown")


# ────────────────────────────────────────
#  /event — 이벤트 정보
# ────────────────────────────────────────
async def event_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_lines = "\n".join(
        f"{i+1}️⃣ {label}: {url}"
        for i, (label, url) in enumerate(config.CHANNEL_INFO)
    )
    await update.message.reply_text(
        f"{config.EVENT_TITLE}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 기간: {config.EVENT_PERIOD}\n"
        f"🎁 리워드: {config.EVENT_REWARD}\n\n"
        f"✅ 참여 채널\n{channel_lines}\n\n"
        f"💡 포인트 지급 방식\n"
        f"• 참여만 해도: +{config.POINTS_JOIN}pt\n"
        f"• 추천인 입력 시: +{config.POINTS_REFER}pt\n"
        f"• 친구 초대 성공 시: +{config.POINTS_INVITED}pt\n"
        f"🎁 [이전 참여자 우대] 초대 마일스톤 달성 시 추가 보너스 지급!" # 한 줄 추가
    )


# ────────────────────────────────────────
#  /help — 명령어 목록
# ────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌙 사용 가능한 명령어\n"
        "━━━━━━━━━━━━━━━\n"
        "/start    — 이벤트 참여 시작\n"
        "/points   — 내 포인트 확인\n"
        "/ranking  — 포인트 랭킹 TOP 10\n"
        "/invite   — 내 초대 정보 + 공유 메시지\n"
        "/event    — 이벤트 상세 정보\n"
        "/skip     — 추천인 입력 건너뛰기\n"
        "/help     — 명령어 목록"
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
    app.add_handler(CommandHandler("invite",  invite_cmd))
    app.add_handler(CommandHandler("event",   event_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))

    logger.info("봇 시작!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
