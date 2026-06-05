"""
mypoints_handler.py
중간 정산 결과 조회 명령어 (/reward, /보상)

bot.py에 추가하려면:
    from mypoints_handler import register_reward_handler
    
    # main() 안에서:
    register_reward_handler(app)
"""
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

# 정산 데이터 로드 (봇 시작 시 1회)
try:
    with open('settlement_data.json', 'r', encoding='utf-8') as f:
        _raw = json.load(f)
    BY_UID = _raw.get('by_uid', {})
    BY_HANDLE = _raw.get('by_handle', {})
    logger.info(f"✅ 정산 데이터 로드: by_uid={len(BY_UID)}명, by_handle={len(BY_HANDLE)}명")
except FileNotFoundError:
    BY_UID = {}
    BY_HANDLE = {}
    logger.warning("⚠️ settlement_data.json 없음 — /mypoints 비활성")


def lookup_settlement(user) -> dict | None:
    """user.id 또는 user.username 으로 매칭. 봇 미가입자가 새로 가입해도 핸들로 찾을 수 있음."""
    uid = str(user.id)
    if uid in BY_UID:
        return BY_UID[uid]
    
    if user.username:
        handle = user.username.strip().lstrip('@').lower()
        if handle in BY_HANDLE:
            return BY_HANDLE[handle]
    
    return None


def format_settlement_msg(data: dict) -> str:
    """정산 결과 메시지 포맷팅 (C안: 카테고리 합계 + 보상 분해)"""
    name = data.get('name') or data.get('handle') or '회원'
    total_pt = data.get('total_points', 0)
    
    msg = "🌙 미드나잇 코리아 포인트 캠페인 중간 정산\n\n"
    msg += f"{name} 님의 정산 내역입니다.\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    # 포인트 있을 때만 내역 표시
    if total_pt > 0:
        msg += "📊 포인트 내역\n"
        if data.get('fi_subtotal', 0) > 0:
            msg += f"🤝 친구초대 이벤트: {data['fi_subtotal']:,}pt\n"
        if data.get('quiz_total', 0) > 0:
            msg += f"📝 퀴즈 (1~5회차): {data['quiz_total']:,}pt\n"
        if data.get('ama_code_pt'):
            msg += f"🎤 AMA 코드 입력: {data['ama_code_pt']:,}pt\n"
        if data.get('holder_pt'):
            msg += f"💎 홀더 인증: {data['holder_pt']:,}pt\n"
        if data.get('kol_pt'):
            msg += f"📣 홀더 인증 (KOL): {data['kol_pt']:,}pt\n"
        msg += "\n━━━━━━━━━━━━━━━━━━\n"
    
    msg += f"✨ 최종 포인트: {total_pt:,}pt\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    # 보상 분해
    rew_pokem = data.get('rew_pokem', 0)
    rew_ama = data.get('rew_ama', 0)
    rew_bracket = data.get('rew_bracket', 0)
    rew_total = data.get('rew_total', 0)
    
    if rew_total > 0:
        msg += "💰 중간 정산 보상\n"
        if rew_pokem:
            msg += f"  • 포캠 확정: ${rew_pokem:.0f}\n"
        if rew_ama:
            msg += f"  • AMA 확정: ${rew_ama:.0f}\n"
        if rew_bracket:
            msg += f"  • 포인트 구간: ${rew_bracket:.0f}\n"
        msg += f"💵 총 지급액: ${rew_total:.2f}\n\n"
    else:
        msg += "💪 이번 중간정산에는 아쉽게 보상에 포함되지 않았지만,\n"
        msg += "남은 포인트 캠페인 동안 이벤트에 더욱 열심히 참여하셔서\n"
        msg += "NIGHT 에어드랍 많이 받아가세요!\n\n"
        msg += "앞으로도 미드나잇 코리아에 많은 관심과 참여부탁드리겠습니다 🌙\n\n"
    
    return msg


async def reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reward — 포인트 획득 내역 + 중간 정산 보상 조회"""
    user = update.effective_user
    data = lookup_settlement(user)
    
    if data is None:
        await update.message.reply_text(
            "📭 정산 내역이 없습니다.\n\n"
            "캠페인에 참여하지 않으셨거나, "
            "텔레그램 핸들 변경 등의 사유로 조회되지 않을 수 있어요.\n\n"
            "문의: @midnight_kor_chat"
        )
        return
    
    msg = format_settlement_msg(data)
    await update.message.reply_text(msg)


def register_reward_handler(app: Application):
    """bot.py 의 main() 에서 호출"""
    app.add_handler(CommandHandler("reward", reward_cmd))
    logger.info("✅ /reward 핸들러 등록 완료")
