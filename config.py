# ─────────────────────────────────────────
#  config.py  —  여기만 수정하면 됩니다
# ─────────────────────────────────────────
import os

# BotFather에서 받은 토큰
BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에_봇토큰_입력")

# 가입 확인할 채널/그룹 목록 (봇을 관리자로 추가해야 함)
REQUIRED_CHANNELS = [
    "@midnight_kor",
    "@midnight_kor_chat",
]

# 채널 표시 이름 + 링크
CHANNEL_INFO = [
    ("미드나잇 한국 공지 채널",     "https://t.me/midnight_kor"),
    ("미드나잇 한국 공식 커뮤니티", "https://t.me/midnight_kor_chat"),
]

# 포인트 설정
POINTS_JOIN    = 100   # /start 기본 지급
POINTS_REFER   = 100   # 추천인 입력 시 본인 추가 지급
POINTS_INVITED = 100   # 추천인에게 지급

# 이벤트 정보 (메시지에 표시됨)
EVENT_TITLE  = "🌙 Midnight Network 한국 친구초대 이벤트"
EVENT_PERIOD = "2026-05-11 ~ 2026-05-25 23:59 KST"
EVENT_REWARD = "총 상금 3천만원 규모 미드나잇 코리아 포인트 캠페인"
