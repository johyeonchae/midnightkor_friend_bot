"""
mypoints_handler.py
NIGHT 에어드랍 정산 결과 조회 명령어 (/reward)

- 1차 / 2차 / 3차 정산 상세 표시
- 최종 에어드랍 지급 주소 (앞 6 / 뒤 6 마스킹)
- 주소 미등록 시 구글폼 안내
- HTML parse_mode 사용 (이탤릭 disclaimer)

bot.py에 추가:
    from mypoints_handler import register_reward_handler
    register_reward_handler(app)   # main() 안에서
"""
import json
import logging
import html
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

# 폼 링크 (지갑 미등록 / 포인트 누락·오류 문의 통합)
WALLET_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSfRGon2D9kpELh4kfcLZ5vsZ8LplP1J84_OWUVn6w5U-7RuQw/viewform?usp=dialog"
FORM_LINK_TEXT = "문의사항 접수 (지갑 주소 오류/ 미등록, 포인트 누락 등)"


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
    logger.warning("⚠️ settlement_data.json 없음 — /reward 비활성")


def lookup_settlement(user) -> dict | None:
    """user.id 또는 user.username 으로 매칭"""
    uid = str(user.id)
    if uid in BY_UID:
        return BY_UID[uid]
    if user.username:
        handle = user.username.strip().lstrip('@').lower()
        if handle in BY_HANDLE:
            return BY_HANDLE[handle]
    return None


def mask_address(addr: str) -> str:
    """앞 6 / 뒤 6 자리로 마스킹"""
    if not addr:
        return ''
    a = str(addr).strip()
    if len(a) <= 14:
        return a
    return f"{a[:6]}...{a[-6:]}"


def format_settlement_msg(data: dict) -> str:
    """정산 결과 메시지 (HTML parse_mode)"""
    name = html.escape(str(data.get('name') or data.get('handle') or '회원'))
    
    msg = "🌙 <b>미드나잇 코리아 포인트 캠페인 정산</b>\n\n"
    msg += f"{name} 님의 정산 내역입니다.\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    # ─── 1차 정산 ───
    s1p = data.get('stage1_points', {})
    s1_amt = data.get('stage1_amount', 0)
    if s1p.get('total', 0) > 0 or s1_amt > 0:
        msg += "━━━ <b>1차 정산 (중간정산)</b> ━━━\n"
        # 세부 내역은 blockquote로
        details = []
        if s1p.get('friend_invite'):
            details.append(f"🤝 친구초대: {s1p['friend_invite']:,}pt")
        if s1p.get('quiz'):
            details.append(f"📝 퀴즈: {s1p['quiz']:,}pt")
        if s1p.get('ama'):
            details.append(f"🎤 AMA 코드: {s1p['ama']:,}pt")
        holder_val = s1p.get('holder', 0) or s1p.get('kol_holder', 0)
        if holder_val:
            details.append(f"💎 네이버페이 홀더 인증: {holder_val:,}pt")
        rew_pokem = data.get('stage1_rew_pokem', 0)
        rew_ama = data.get('stage1_rew_ama', 0)
        if rew_pokem > 0:
            details.append(f"🏆 포캠 선착순 확정: +${rew_pokem:,.0f}")
        if rew_ama > 0:
            details.append(f"🏆 AMA 선착순 확정: +${rew_ama:,.0f}")
        if details:
            msg += "<blockquote>" + "\n".join(details) + "</blockquote>\n"
        msg += f"💵 <b>지급액: ${s1_amt:,.2f}</b>\n\n"
    
    # ─── 2차 정산 ───
    s2p = data.get('stage2_points', {})
    s2_total = data.get('stage2_amount_total', 0)
    s2_confirmed_type = data.get('stage2_confirmed_type')
    s2_extra = data.get('stage2_amount_extra', 0)
    if s2p.get('total', 0) > 0 or s2_total > 0:
        msg += "━━━ <b>2차 정산 (야핑 · 차트 아티스트)</b> ━━━\n"
        details = []
        if s2p.get('yaping'):
            details.append(f"🔥 야핑: {s2p['yaping']:,}pt")
        if s2p.get('chart'):
            details.append(f"🎨 차트 아티스트: {s2p['chart']:,}pt")
        if s2_confirmed_type and s2_extra > 0:
            details.append(f"🏆 {html.escape(s2_confirmed_type)} 확정: +${s2_extra:,.0f}")
        if details:
            msg += "<blockquote>" + "\n".join(details) + "</blockquote>\n"
        msg += f"💵 <b>지급액: ${s2_total:,.2f}</b>\n\n"
    
    # ─── ADA 선착순 700 ───
    ada_amt = data.get('ada_bonus_amount', 0)
    if ada_amt > 0:
        msg += "━━━ <b>ADA 선착순 700</b> ━━━\n"
        msg += f"💵 <b>지급액: ${ada_amt:,.2f}</b>\n\n"
    
    # ─── X 데일리 미션 ───
    s3_days = data.get('stage3_days', 0)
    s3_amt = data.get('stage3_amount', 0)
    s3_bonus = data.get('stage3_attendance_bonus', 0)
    if s3_days > 0 or s3_amt > 0:
        msg += "━━━ <b>X 데일리 미션</b> ━━━\n"
        msg += f"<blockquote>📆 참여일수: {s3_days}일</blockquote>\n"
        if s3_bonus > 0:
            msg += f"💵 <b>지급액: ${s3_amt:,.2f}</b> (만근 보너스 +${s3_bonus:,.0f})\n\n"
        else:
            msg += f"💵 <b>지급액: ${s3_amt:,.2f}</b>\n\n"
    
    # ─── 최종 총액 ───
    total = data.get('total_amount', 0)
    msg += f"💰 <b>최종 에어드랍 총액: ${total:,.2f}</b>\n\n"
    
    # ─── 지급 주소 or 미등록 안내 ───
    addr = data.get('airdrop_address', '')
    chain = data.get('chain', '')
    if addr and chain and chain != '주소없음':
        masked = html.escape(mask_address(addr))
        chain_safe = html.escape(str(chain))
        msg += f"📤 <b>지급 주소 ({chain_safe})</b>\n"
        msg += f"<code>{masked}</code>\n\n"
    else:
        msg += "⚠️ <b>지갑 주소가 등록되지 않았습니다.</b>\n"
        msg += "아래 폼에서 지갑 주소를 제출해주세요.\n\n"
    
    # ─── 구분선 ───
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    # ─── 문의 폼 링크 ───
    msg += f'🔗<a href="{WALLET_FORM_URL}">{FORM_LINK_TEXT}</a>\n'
    
    # ─── Disclaimer (이탤릭) ───
    msg += "<i>※ 지급 당시 $NIGHT 기준이며, "
    msg += "지급 시점 및 상황에 따라 일부 가격 변동이 있을 수 있습니다.</i>"
    
    return msg


async def reward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reward — 정산 내역 + 지급 주소 조회"""
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
    
    # 어뷰징 판정으로 제외된 계정
    if data.get('chain') == '제외':
        await update.message.reply_text(
            "⚠️ <b>정산 대상에서 제외된 계정입니다.</b>\n\n"
            "운영 정책에 따른 검수 과정에서 "
            "비정상 참여로 판정되어 보상 지급 대상에서 제외되었습니다.\n\n"
            f'이의가 있으신 경우 아래 폼으로 접수해주세요.\n'
            f'🔗<a href="{WALLET_FORM_URL}">{FORM_LINK_TEXT}</a>',
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return
    
    msg = format_settlement_msg(data)
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def register_reward_handler(app: Application):
    """bot.py 의 main() 에서 호출"""
    app.add_handler(CommandHandler("reward", reward_cmd))
    logger.info("✅ /reward 핸들러 등록 완료")
