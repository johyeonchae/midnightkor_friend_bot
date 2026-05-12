# ─────────────────────────────────────────
#  bot.py  —  업데이트본
# ─────────────────────────────────────────
import logging
import os
import csv
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
#  유틸 함수
# ────────────────────────────────────────
def is_loyalty_user(username: str) -> bool:
    if not username:
        return False
    return username.lstrip("@").lower() in config.LOYALTY_USERS


def is_private_chat(update: Update) -> bool:
    """1:1 DM 인지 확인"""
    return update.effective_chat and update.effective_chat.type == "private"


async def warn_private_only(update: Update):
    """그룹에서 봇 명령 호출 시 안내 메시지"""
    await update.message.reply_text(
        f"👉 봇과 1:1 대화방에서 시작해주세요! @{config.BOT_USERNAME}"
    )


def parse_deep_link_referrer(args, current_user_id: int) -> int | None:
    """딥링크 인자에서 추천인 ID 파싱. 형식: ref{user_id}"""
    if not args:
        return None
    arg = args[0]
    if not arg.startswith("ref"):
        return None
    try:
        ref_id = int(arg[3:])
        if ref_id > 0 and ref_id != current_user_id:
            return ref_id
    except ValueError:
        pass
    return None


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
    # 그룹/슈퍼그룹에서 /start 차단 (DM 못 보내서 박제되는 버그 방지)
    if not is_private_chat(update):
        await warn_private_only(update)
        return ConversationHandler.END

    user = update.effective_user

    # 딥링크 추천인 파싱 → user_data 에 임시 저장
    ref_id = parse_deep_link_referrer(context.args, user.id)
    if ref_id:
        context.user_data["pending_referrer_id"] = ref_id

    # 이미 등록된 유저
    existing = await db.get_user(
        user.id, current_username=user.username, current_full_name=user.full_name
    )
    if existing:
        invite_count = await db.get_referral_count(user.id)
        status = "완료 ✅" if existing["referral_done"] else "미입력"

        msg = (
            f"이미 참여 중이세요, {existing['full_name']}님 🌙\n\n"
            f"🏆 보유 포인트: {existing['points']}pt\n"
            f"👥 초대한 친구: {invite_count}명\n"
            f"📎 추천인 입력: {status}\n"
        )
        # 아직 추천인 보너스 못 받은 사람 (예: 그룹에서 잘못 등록된 유저, 옛날 /skip 한 유저)
        if not existing["claimed_referrer_bonus"]:
            msg += (
                f"\n💡 추천인을 아직 입력하지 않으셨네요!\n"
                f"/referral 명령어로 입력 시 +{config.POINTS_REFER}pt 추가 적립됩니다.\n"
            )
        msg += "\n내 @username을 친구에게 알려 함께 포인트를 쌓아보세요!"

        await update.message.reply_text(msg)
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
    user = query.from_user
    await query.answer()

    # 그룹에서 버튼 누른 경우 차단
    if not is_private_chat(update):
        await query.edit_message_text(
            f"👉 봇과 1:1 대화방에서 시작해주세요! @{config.BOT_USERNAME}"
        )
        return ConversationHandler.END

    existing = await db.get_user(
        user.id, current_username=user.username, current_full_name=user.full_name
    )
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
    username = user.username or ""
    full_name = user.full_name or str(user.id)
    await db.register_user(user.id, username, full_name)

    # 우대 대상자 환영 문구
    loyalty_text = ""
    if is_loyalty_user(username):
        loyalty_text = (
            "🌟 [특별 우대 대상자 선정 안내]\n"
            "이전 이벤트 참여 기록이 확인되어 특별 우대 대상으로 등록되셨습니다!\n"
            "친구 초대 달성 시마다 추가 보너스 pt가 자동 지급됩니다. (최대 1,900pt 추가 보상)\n"
            "자세한 내용은 /points 로 확인해 보세요!\n"
            "━━━━━━━━━━━━━━━\n\n"
        )

    # 메시지 1 — 이벤트 소개 + 환영
    await context.bot.send_message(
        user.id,
        f"{config.EVENT_TITLE}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{loyalty_text}"
        f"📅 기간: {config.EVENT_PERIOD}\n"
        f"🎁 리워드: {config.EVENT_REWARD}\n\n"
        f"🎉 {full_name}님, 환영합니다!\n"
        f"참여 보상으로 {config.POINTS_JOIN}pt가 지급됐어요.\n\n"
        f"친구를 초대하면 나와 친구 모두 {config.POINTS_INVITED}pt씩 추가 지급됩니다.\n"
        "포인트는 미드나잇 코리아 캠페인에 누적되며 종료 후 리워드로 환산됩니다.\n\n"
        "⚠️ 이벤트 종료 전까지 채널에 남아 계셔야 포인트가 유지됩니다. "
        "채널을 나가면 본인과 추천인 포인트가 모두 차감됩니다!",
    )

    # 딥링크로 들어온 경우 → 추천인 자동 적용
    pending_ref_id = context.user_data.pop("pending_referrer_id", None)
    if pending_ref_id:
        referrer = await db.get_user(pending_ref_id)
        if referrer:
            await db.set_referral_done(user.id, referrer["user_id"])
            bonus_pt = await db.process_loyalty_bonus(
                referrer["user_id"], referrer["username"]
            )

            # 추천인에게 DM 알림
            try:
                invite_count = await db.get_referral_count(referrer["user_id"])
                refreshed = await db.get_user(referrer["user_id"])
                msg = (
                    f"🎉 @{user.username or user.full_name} 님이 회원님의 초대로 참여했습니다!\n"
                    f"👉 +{config.POINTS_INVITED}pt 적립!\n"
                )
                if bonus_pt > 0:
                    msg += f"🎁 [우대 혜택] 초대 목표 달성 보너스 {bonus_pt}pt 추가 적립!\n"
                msg += f"🏆 현재 누적 포인트: {refreshed['points']}pt\n"
                msg += f"📊 총 초대: {invite_count}명"
                await context.bot.send_message(referrer["user_id"], msg)
            except Exception as e:
                logger.warning(f"추천인 알림 실패: {e}")

            # 가입자에게 자동 적용 안내
            me = await db.get_user(user.id)
            await context.bot.send_message(
                user.id,
                f"✅ 추천인 자동 적용 완료!\n\n"
                f"🎉 나에게 +{config.POINTS_REFER}pt 추가 적립!\n"
                f"🎉 @{referrer['username']}님께도 +{config.POINTS_INVITED}pt 지급!\n\n"
                f"🏆 현재 내 포인트: {me['points']}pt\n\n"
                f"내 @username을 친구에게 공유해서 함께 포인트를 쌓아보세요 🌙",
            )
            return ConversationHandler.END
        else:
            logger.info(f"딥링크 추천인 ID {pending_ref_id} 미등록 유저 — 수동 입력으로 진행")

    # 메시지 2 — 추천인 입력 요청 (공식 추천인 옵션 안내)
    await context.bot.send_message(
        user.id,
        f"👥 추천인을 입력해주세요!\n\n"
        f"• 친구가 초대한 경우: 친구의 @username 입력\n"
        f"  → 나 +{config.POINTS_REFER}pt, 친구 +{config.POINTS_INVITED}pt\n\n"
        f"• 추천인이 없는 경우: @{config.OFFICIAL_REFERRER} 입력\n"
        f"  → 나 +{config.POINTS_REFER}pt\n\n"
        f"(예: @username 또는 @{config.OFFICIAL_REFERRER})",
    )
    return WAITING_REFERRER


# ────────────────────────────────────────
#  추천인 입력 처리
# ────────────────────────────────────────
async def receive_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_private_chat(update):
        return WAITING_REFERRER

    user = update.effective_user
    text = update.message.text.strip()
    clean = text.lstrip("@").lower()

    # 자기 자신 방지
    if user.username and clean == user.username.lower():
        await update.message.reply_text(
            "❌ 자기 자신을 추천인으로 입력할 수 없어요.\n"
            f"다른 @유저네임을 입력하거나 @{config.OFFICIAL_REFERRER} 를 입력하세요."
        )
        return WAITING_REFERRER

    # ⭐ 공식 추천인 (@midnight_kor) 분기
    if clean == config.OFFICIAL_REFERRER.lower():
        awarded = await db.set_official_referrer(user.id)
        if awarded == 0:
            await update.message.reply_text(
                "❌ 이미 추천인 보너스를 받으셨어요."
            )
            return ConversationHandler.END
        me = await db.get_user(user.id)
        await update.message.reply_text(
            f"✅ 공식 추천인 적용 완료!\n\n"
            f"🎉 +{config.POINTS_REFER}pt 적립!\n"
            f"🏆 현재 내 포인트: {me['points']}pt\n\n"
            f"이제 내 @username을 친구에게 공유하면\n"
            f"친구 1명당 둘 다 +{config.POINTS_INVITED}pt 추가 적립됩니다 🌙"
        )
        return ConversationHandler.END

    # 일반 추천인 조회
    referrer = await db.get_user_by_username(text)
    if not referrer:
        await update.message.reply_text(
            f"❌ @{clean} 님은 아직 이벤트에 참여하지 않으셨어요.\n"
            f"올바른 @유저네임을 입력하거나 추천인이 없다면 @{config.OFFICIAL_REFERRER} 를 입력하세요."
        )
        return WAITING_REFERRER

    # 이미 보너스 받은 경우 차단 (재진입 방지)
    me_check = await db.get_user(user.id)
    if me_check and me_check["claimed_referrer_bonus"]:
        await update.message.reply_text(
            "❌ 이미 추천인 보너스를 받으셨어요."
        )
        return ConversationHandler.END

    # 포인트 지급 + 관계 저장
    await db.set_referral_done(user.id, referrer["user_id"])

    # 추천인(Inviter)이 우대 대상자인 경우 보너스 체크
    bonus_pt = await db.process_loyalty_bonus(
        referrer["user_id"], referrer["username"]
    )

    # 추천인에게 DM 알림
    try:
        invite_count = await db.get_referral_count(referrer["user_id"])
        refreshed_referrer = await db.get_user(referrer["user_id"])
        msg = (
            f"🎉 @{user.username or user.full_name} 님이 회원님의 초대로 참여했습니다!\n"
            f"👉 +{config.POINTS_INVITED}pt 적립!\n"
        )
        if bonus_pt > 0:
            msg += f"🎁 [우대 혜택] 초대 목표 달성 보너스 {bonus_pt}pt 추가 적립!\n"

        msg += f"🏆 현재 누적 포인트: {refreshed_referrer['points']}pt\n"
        msg += f"📊 총 초대: {invite_count}명"

        await context.bot.send_message(referrer["user_id"], msg)
    except Exception as e:
        logger.warning(f"추천인 알림 실패: {e}")

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
#  /skip — 이제는 안내만 보여주고 상태 유지
# ────────────────────────────────────────
async def skip_referrer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_private_chat(update):
        return WAITING_REFERRER

    await update.message.reply_text(
        f"💡 추천인을 꼭 입력해주세요!\n\n"
        f"• 친구의 @username 입력 → 둘 다 +{config.POINTS_REFER}pt\n"
        f"• 추천인이 없다면 @{config.OFFICIAL_REFERRER} 입력 → 나 +{config.POINTS_REFER}pt\n\n"
        f"어느 쪽이든 +{config.POINTS_REFER}pt 추가 적립됩니다!"
    )
    return WAITING_REFERRER


# ────────────────────────────────────────
#  /referral — 나중에 추천인 입력 (스킵자 구제)
# ────────────────────────────────────────
async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_private_chat(update):
        await warn_private_only(update)
        return ConversationHandler.END

    user = update.effective_user
    existing = await db.get_user(
        user.id, current_username=user.username, current_full_name=user.full_name
    )

    if not existing:
        await update.message.reply_text(
            "아직 참여하지 않으셨어요.\n/start 로 이벤트를 시작해주세요!"
        )
        return ConversationHandler.END

    # 🔒 이미 보너스 받은 사람 차단 (실제 추천인 입력자 OR 공식 추천인 사용자)
    if existing["claimed_referrer_bonus"]:
        await update.message.reply_text(
            "❌ 추천인은 한 번만 입력할 수 있어요.\n"
            "이미 입력하셨거나 공식 추천인을 사용하셨습니다."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"👥 추천인을 입력해주세요!\n\n"
        f"• 친구의 @username 입력\n"
        f"  → 나 +{config.POINTS_REFER}pt, 친구 +{config.POINTS_INVITED}pt\n\n"
        f"• @{config.OFFICIAL_REFERRER} 입력 (추천인 없는 경우)\n"
        f"  → 나 +{config.POINTS_REFER}pt\n\n"
        f"(예: @username 또는 @{config.OFFICIAL_REFERRER})"
    )
    return WAITING_REFERRER


# ────────────────────────────────────────
#  /points — 내 포인트 확인
# ────────────────────────────────────────
async def points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        await warn_private_only(update)
        return

    user = update.effective_user
    existing = await db.get_user(
        user.id, current_username=user.username, current_full_name=user.full_name
    )
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
    total = await db.get_total_participants()
    existing = await db.get_user(user.id)  # 최신 상태 재조회

    loyalty_info = ""
    if is_loyalty_user(user.username):
        loyalty_info = (
            "🌟 특별 우대 대상자\n"
            "초대 마일스톤 보너스 적용 중:\n"
            "• 1명(+100pt) / 3명(+300pt)\n"
            "• 5명(+500pt) / 10명(+1000pt)\n"
            "━━━━━━━━━━━━━━━\n"
        )

    referrer_status = "완료 ✅" if existing["claimed_referrer_bonus"] else "미입력 (/referral 로 입력 가능)"

    await update.message.reply_text(
        f"📊 내 이벤트 현황\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{loyalty_info}"
        f"👤 닉네임: {existing['full_name']}\n"
        f"🏆 보유 포인트: {existing['points']}pt\n"
        f"👥 초대한 친구: {invite_count}명\n"
        f"📎 추천인 입력: {referrer_status}\n"
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
    lines = ["🏆 포인트 랭킹 TOP 10\n━━━━━━━━━━━━━━━"]
    for i, row in enumerate(board):
        # 닉네임 표시 (username 있으면 @핸들, 없으면 full_name)
        if row.get("username"):
            name = f"@{row['username']}"
        else:
            name = row.get("full_name") or "익명"
        # 너무 긴 이름은 잘라서 표시
        if len(name) > 20:
            name = name[:18] + "…"
        lines.append(
            f"{medals[i]} {i+1}위 {name} — "
            f"{row['points']}pt (초대 {row['invite_count']}명)"
        )
    await update.message.reply_text("\n".join(lines))


# ────────────────────────────────────────
#  /invite — 내 초대 정보 + 딥링크
# ────────────────────────────────────────
async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private_chat(update):
        await warn_private_only(update)
        return

    user = update.effective_user
    existing = await db.get_user(
        user.id, current_username=user.username, current_full_name=user.full_name
    )
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
    deep_link = f"https://t.me/{config.BOT_USERNAME}?start=ref{user.id}"

    msg = f"📤 내 초대 정보\n━━━━━━━━━━━━━━━\n"
    msg += f"내 @username: @{user.username}\n"

    if is_loyalty_user(user.username):
        msg += "\n🌟 [특별 우대 대상자 혜택]\n"
        msg += "초대 달성 시마다 pt가 자동 추가됩니다!\n"
        msg += "• 1명(+100pt) / 3명(+300pt) / 5명(+500pt) / 10명(+1000pt)\n"

    msg += f"\n👥 지금까지 초대한 친구: {invite_count}명\n"
    msg += f"🏆 보유 포인트: {existing['points']}pt\n"
    msg += "━━━━━━━━━━━━━━━\n\n"
    msg += "👇 친구에게 아래 메시지를 그대로 공유하세요!\n\n"
    msg += "─ ─ ─ ─ ─ ─ ─ ─\n"
    msg += f"🌙 Midnight Network 친구초대 이벤트!\n"
    msg += f"아래 링크 클릭 → /start 누르면 자동 추천 적용 + {config.POINTS_JOIN + config.POINTS_REFER}pt 즉시 지급!\n\n"
    msg += f"🔗 원클릭 참여 링크:\n{deep_link}\n\n"
    msg += f"수동 입력하실 분은 @{config.BOT_USERNAME} 에서 /start 후\n"
    msg += f"추천인에 @{user.username} 입력!"

    await update.message.reply_text(msg)


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
        f"  (친구 @username 또는 @{config.OFFICIAL_REFERRER})\n"
        f"• 친구 초대 성공 시: +{config.POINTS_INVITED}pt\n"
        f"🎁 [이전 참여자 우대] 초대 마일스톤 달성 시 추가 보너스 지급!"
    )


# ────────────────────────────────────────
#  /help — 명령어 목록
# ────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌙 사용 가능한 명령어\n"
        "━━━━━━━━━━━━━━━\n"
        "/start    — 이벤트 참여 시작\n"
        "/referral — 추천인 나중에 입력 (미입력자만)\n"
        "/points   — 내 포인트 확인\n"
        "/ranking  — 포인트 랭킹 TOP 10\n"
        "/invite   — 내 초대 정보 + 공유 링크\n"
        "/event    — 이벤트 상세 정보\n"
        "/help     — 명령어 목록"
    )


# ────────────────────────────────────────
#  전역 에러 핸들러
# ────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)


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
            CommandHandler("referral", referral_cmd),
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
        allow_reentry=True,           # WAITING_REFERRER 상태에서 /start, /referral 재진입 허용
        conversation_timeout=600,     # 10분 후 자동 종료 ([job-queue] 필요)
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(verify_join_callback, pattern="^verify_join$"))
    app.add_handler(CommandHandler("points",  points_cmd))
    app.add_handler(CommandHandler("ranking", ranking_cmd))
    app.add_handler(CommandHandler("invite",  invite_cmd))
    app.add_handler(CommandHandler("event",   event_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_error_handler(error_handler)

    logger.info("봇 시작!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
