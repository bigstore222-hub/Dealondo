"""
notify.py — 텔레그램 알림

FLASH 딜은 정기 슬롯을 기다리지 않고 즉시 푸시한다.
품절 반응이 4년간 반복됐듯 좋은 딜일수록 수명이 짧기 때문이다.

설정 (봇 토큰 발급 5분):
  1) 텔레그램에서 @BotFather 검색 → /newbot → 봇 이름 입력 → 토큰 받기
  2) 만든 봇과 대화 시작(아무 메시지나 전송)
  3) https://api.telegram.org/bot<토큰>/getUpdates 열어서 chat.id 확인
  4) 환경변수 설정:
       TELEGRAM_BOT_TOKEN=123456:ABC...
       TELEGRAM_CHAT_ID=123456789

토큰이 없으면 콘솔 출력으로 자동 폴백하므로 설정 전에도 파이프라인은 돌아간다.
"""
from __future__ import annotations
import os, re, json, urllib.request, urllib.parse

API = "https://api.telegram.org/bot{token}/sendMessage"

# 콘솔 출력용 이모지 대체표 (윈도우 CP949 대응)
_CONSOLE_SAFE = {
    "🔴": "[FLASH]", "🟠": "[HOT]", "🟡": "[STEADY]",
    "💰": "가격", "🎟": "[쿠폰]", "❄": "[역시즌]", "🎁": "[사은품]",
    "📉": "[역대최저]", "⏰": "[마감]", "📬": "[새딜]",
}


def _ascii_only() -> bool:
    """현재 콘솔 인코딩이 한글조차 못 찍는 환경인지."""
    import sys
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return enc in ("ascii", "us-ascii")


URGENCY_ICON = {"FLASH": "🔴", "HOT": "🟠", "STEADY": "🟡"}

# 브랜드 가치 배지. 한눈에 핫딜을 구별하는 핵심 신호다.
#   ⭐ 프리미엄 · 🔷 유명 · ⚠ 무명(가치 미검증) · (전자제품 등은 배지 없음)
TIER_BADGE = {"premium": "⭐", "known": "🔷", "unknown": "⚠"}


def _esc(s: str) -> str:
    """텔레그램 HTML 파스모드용 이스케이프."""
    import html as _html
    return (_html.unescape(str(s))          # &#39; 같은 HTML 엔티티 먼저 복원
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def brand_label(d, with_rest: bool = True, maxlen: int = 46) -> str:
    """
    '⭐<b>브랜드</b> 나머지제목' 형태로 브랜드를 앞세운다.
    제목이 브랜드로 시작하면 중복을 제거한다(예: 'Skechers Skechers ...' 방지).
    브랜드가 없으면 제목만.
    """
    brand = (d.brand or "").strip()
    title = (d.title or "").strip()
    badge = TIER_BADGE.get(getattr(d, "brand_tier", ""), "")
    if not brand:
        return _esc(title[:maxlen])
    rest = title
    if title.lower().startswith(brand.lower()):
        rest = title[len(brand):].lstrip(" -·,·").strip()
    head = f"{badge}<b>{_esc(brand)}</b>" if badge else f"<b>{_esc(brand)}</b>"
    if with_rest and rest:
        head += f" {_esc(rest[:maxlen])}"
    return head


def format_deal(d) -> str:
    icon = URGENCY_ICON.get(d.urgency, "")
    lines = [f"{icon} <b>{_esc(d.urgency)}</b>  ·  {d.score}점  ·  {_esc(d.source)}"]

    # 브랜드를 앞세워 도드라지게(티어 배지 포함). 제목은 그 뒤에 이어붙는다.
    lines.append(brand_label(d, with_rest=True, maxlen=90))

    if d.price_current is not None:
        cur = f"${d.price_current:,.2f}" if d.price_current < 100000 else f"{d.price_current:,.0f}원"
        price = f"💰 <b>{cur}</b>"
        if d.price_list:
            was = f"${d.price_list:,.2f}" if d.price_list < 100000 else f"{d.price_list:,.0f}원"
            price += f"  <s>{was}</s>"
        if d.discount_pct:
            price += f"  <b>{d.discount_pct:.0f}% OFF</b>"
        lines.append(price)

    tags = []
    if d.coupon_stackable:
        tags.append(f"🎟 쿠폰중복{(' ' + _esc(d.coupon_code)) if d.coupon_code else ''}")
    if d.off_season:
        tags.append("❄ 역시즌")
    if d.free_gift:
        tags.append("🎁 사은품")
    if d.price_alltime_low and d.price_current and d.price_current <= d.price_alltime_low:
        tags.append("📉 역대최저")
    if d.expires_at:
        tags.append(f"⏰ ~{_esc(d.expires_at)[:16].replace('T', ' ')}")
    if tags:
        lines.append(" · ".join(tags))

    link = getattr(d, "buy_url", "") or d.url
    lines.append(f'\n<a href="{_esc(link)}">딜 보러가기 →</a>')
    return "\n".join(lines)


def send(text: str, disable_preview: bool = False) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        # 윈도우 콘솔(CP949)은 이모지를 못 찍는다.
        # 텔레그램으로 보낼 때는 이모지를 그대로 쓰되,
        # 화면 출력 시에는 안전한 문자로 바꿔서 인코딩 오류를 막는다.
        plain = re.sub(r"<[^>]+>", "", text)
        for emo, alt in _CONSOLE_SAFE.items():
            plain = plain.replace(emo, alt)
        plain = plain.encode("ascii", "ignore").decode() if _ascii_only() else plain
        print("-" * 50)
        print("[알림 미설정 - 화면 출력]")
        print(plain)
        print("-" * 50)
        return False

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true" if disable_preview else "false",
    }).encode()

    try:
        req = urllib.request.Request(API.format(token=token), data=payload)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"[notify] 전송 실패: {type(e).__name__}: {str(e)[:120]}")
        return False


def notify_deals(deals: list, digest_threshold: int = 5) -> int:
    """
    FLASH는 개별 즉시 전송(각각 눈에 띄어야 한다).
    HOT/STEADY가 많으면 묶어서 다이제스트 한 통으로 보낸다(알림 피로 방지).
    """
    flash = [d for d in deals if d.urgency == "FLASH"]
    rest = [d for d in deals if d.urgency != "FLASH"]
    sent = 0

    for d in flash:
        if send(format_deal(d)):
            sent += 1

    if not rest:
        return sent

    if len(rest) <= digest_threshold:
        for d in rest:
            if send(format_deal(d)):
                sent += 1
    else:
        head = (f"📬 <b>새 딜 {len(rest)}건</b>\n"
                f"<i>⭐프리미엄 · 🔷유명 · ⚠무명</i>\n")
        body = []
        for d in sorted(rest, key=lambda x: -x.score)[:15]:
            icon = URGENCY_ICON.get(d.urgency, "")
            pct = f"{d.discount_pct:.0f}%" if d.discount_pct else "-"
            code = f" 🎟{_esc(d.coupon_code)}" if getattr(d, "coupon_code", "") else ""
            # 브랜드를 앞세운 라벨을 링크로. 한눈에 브랜드·가치가 보인다.
            label = brand_label(d, with_rest=True, maxlen=42)
            link = getattr(d, "buy_url", "") or d.url
            body.append(f'{icon} <a href="{_esc(link)}">{label}</a>  '
                        f'{pct} · {d.score}점{code}')
        if send(head + "\n".join(body), disable_preview=True):
            sent += 1
    return sent
