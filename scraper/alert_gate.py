"""
alert_gate.py — 텔레그램 알림 전용 '구매 가치' 게이트

딜보드(웹)는 넓게 보여주고, 텔레그램 알림은 '지금 사도 되는 현실적인 딜'만 보낸다.
사용자 방침: "실제 구매 가치가 없는데 할인율만 높다고 보내면 쓰레기다."

실측(10/30 딜보드 87건)에서 걸러야 했던 것
  · 세일 공지("Up to 70% off", "Specials from $29", "Friends & Family") — 살 상품이 특정되지 않음
  · 할인율을 모르는 딜(정가 없음 0%) — 핫딜인지 판단 불가
  · 무명 잡화(문매트·핸드워머·차량 거치대·크리스마스 장식) — 브랜드 없는 50%는 원래 가격이 의미 없음
  · 브랜드 오인식("Acne Patches"→Acne Studios, "Monster Hunter"→Hunter, 점프스타터→Diesel)
  · 디지털 코드(PC 게임 $3.40), 도수 안경테 — 직구 실익이 없거나 그 자체로 못 씀
  · 점수 50~69(STEADY) — 딜보드에는 두되 알림까지 보낼 급은 아님

통과 조건(모두 만족)
  1) 점수 70점 이상(HOT·FLASH)
  2) 살 상품과 가격이 특정됨(가격 > 0, 세일 공지 아님)
  3) 할인율을 알고, 품목별 기준 이상 (패션은 하드필터 H7이 이미 50~60%+ 보장 / 테크 25% / 기타 40%)
  4) 브랜드가 확실함(유명 패션 브랜드 사전 또는 테크·생활 브랜드 목록) — 무명·오인식 제외
  5) 디지털 코드·안경테·기프트카드가 아님
  6) $15 미만 단품은 80% 이상 할인일 때만(배대지 배송비 대비 실익)
  7) 같은 브랜드는 한 번에 2건까지
  8) 한국 기준 실익: 국내 최저가를 알면 15% 이상 싸야 하고,
     면세한도($200, 무선기기 $150)를 넘는데 국내가를 모르면 85점 이상만
환경변수로 조정: RADAR_ALERT_MIN_SCORE, RADAR_ALERT_TECH_MIN, RADAR_ALERT_ETC_MIN, RADAR_ALERT_MAX_PER_RUN
"""
from __future__ import annotations
import os, re

import brands
import filter_engine as fe

MIN_SCORE = int(os.environ.get("RADAR_ALERT_MIN_SCORE", "60"))
TECH_MIN = float(os.environ.get("RADAR_ALERT_TECH_MIN", "40"))  # 2026-11-20 25→40
ETC_MIN = float(os.environ.get("RADAR_ALERT_ETC_MIN", "40"))
KR_SAVING_MIN = float(os.environ.get("RADAR_ALERT_KR_SAVING", "15"))
MAX_PER_RUN = int(os.environ.get("RADAR_ALERT_MAX_PER_RUN", "8"))
PER_BRAND = int(os.environ.get("RADAR_ALERT_PER_BRAND", "3"))       # 같은 브랜드 도배 방지
LOW_PRICE = float(os.environ.get("RADAR_ALERT_LOW_PRICE", "10"))     # 이보다 싼 단품은 배대지 요금 대비 실익이 적다

# 테크·생활 브랜드 목록은 brands.py 로 옮김(스코어링과 함께 쓰기 위해)
TECH_BRANDS = brands.TECH_BRANDS
_TECH = sorted(TECH_BRANDS, key=len, reverse=True)

ROUNDUP = re.compile(
    r"\bup to \d+%|\bdeals? (at|on)\b|\bsale\b\s*:|\bspecials?\b|friends\s*&\s*family|\boutlet deals\b|"
    r"% off select|\bfrom \$\d|\bclearance event\b|\bsitewide\b|\bstorewide\b|\bextra \d+% off\b(?!.*\$\d)",
    re.I)
DIGITAL = re.compile(r"digital (delivery|download|code)|\bfor pc\b|\bsteam\b|\be-?gift\b|gift card|"
                     r"\bsubscription\b|\bdownload\b|\bebook\b|\bkindle edition\b", re.I)
NOT_USABLE = re.compile(r"eyeglass|optical frame|prescription|\breading glasses\b", re.I)
WIRELESS = re.compile(r"\b(bluetooth|wireless|earbuds?|airpods|headphones?|smart ?watch|apple watch|"
                      r"galaxy watch|pixel watch|fitbit|garmin|tws|earphones?|wi-?fi|ipad|tablet|kindle|"
                      r"macbook|laptop|speaker)\b", re.I)
AGGREGATORS = ("dealnews", "slickdeals", "techbargains", "dealsofamerica", "woot")


def _g(d, k, default=None):
    return d.get(k, default) if isinstance(d, dict) else getattr(d, k, default)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("’", "'")).strip()


def _lead(title: str) -> str:
    """제목 앞의 수량·잡음을 떼어 '브랜드로 시작하는 부분'을 만든다. 예) '2-Pk Wegear ...' → 'wegear ...'
    2026-11-20: 슬릭딜 제목 머리말도 뗀다 — 'Refurb:', 'Prime Members:', '[NEW] $12.99 |', '$11.99:', '44mm', '31L'"""
    t = _norm(title)
    for _ in range(4):
        t0 = t
        t = re.sub(r"^(\[[^\]]*\]\s*|\([^)]{0,30}\)\s*|\(?(new|used|open box|certified)?/?\s*(open box)?\)?\s*refurb(ished)?\)?:?\s*|"
                   r"prime( members?)?:?\s*|\$[\d.,]+\*?\s*[:|]\s*|"
                   r"\d+(\.\d+)?\s*(mm|l|\"|''|in|inch|oz|gb|tb)\b[\s,-]*|"
                   r"\d+[- ]?(pk|pack|pc|pcs|piece|ct|count|pair)s?\b[\s,:-]*)", "", t)
        t = re.sub(r"^(new|refurb(ished)?|open box|men's|women's|kids'?|unisex)\s+", "", t)
        if t == t0:
            break
    return t


def brand_ok(d) -> tuple[bool, str]:
    """브랜드가 확실한가. 딜 사이트 제목은 브랜드로 시작해야 한다(본문 중간 단어로 오인식 방지)."""
    src = (_g(d, "source") or "").lower()
    title, brand = _g(d, "title") or "", _g(d, "brand") or ""
    lead = _lead(title)
    if any(a in src for a in AGGREGATORS):
        tier, canon = brands.lookup(" ".join(lead.split()[:4]))
        if tier != "unknown" and lead.startswith(_norm(canon).split()[0]):
            return True, canon
        for b in _TECH:
            if re.match(rf"{re.escape(b)}(?![a-z])", lead):
                return True, b
        # 슬릭딜 글 상세에 적힌 브랜드(작성자가 지정) — 사전·테크 목록에 있는 브랜드면 인정
        sb = ((_g(d, "signals") or {}).get("sd_brand") or "")
        if sb and (brands.tier(sb) != "unknown" or brands.tech_brand(sb)) and _norm(sb).split()[0] in lead:
            return True, sb
        return False, "브랜드 불명확(제목이 알려진 브랜드로 시작하지 않음)"
    # 쇼핑몰 직접 수집: 상품 페이지의 브랜드 칸
    if brands.tier(brand) != "unknown":
        return True, brand
    nb = _norm(brand)
    if any(re.match(rf"{re.escape(b)}(?![a-z])", nb) for b in _TECH):
        return True, brand
    return False, f"무명 브랜드({brand or '미상'})"


def worth_alert(d) -> tuple[bool, str]:
    score = _g(d, "score") or 0
    title = _g(d, "title") or ""
    price = _g(d, "price_current") or 0
    pct = _g(d, "discount_pct")
    cat = _g(d, "category") or "etc"
    if score < MIN_SCORE:
        return False, f"점수 {score} < {MIN_SCORE}"
    if not price or price <= 0 or ROUNDUP.search(title):
        return False, "세일 공지(살 상품·가격이 특정되지 않음)"
    if DIGITAL.search(title):
        return False, "디지털 상품"
    if NOT_USABLE.search(title):
        return False, "안경테 등 직구 실익 없음"
    if price < LOW_PRICE and not (pct and pct >= 80):
        # 2026-11-20: 알려진 브랜드 60%+ 저가 소품(아디다스 헤어밴드·플레이도우·멜리사앤더그)은 Bexel 이 꾸준히 올림 → 통과
        if not (pct and pct >= 60 and brand_ok(d)[0]):
            return False, f"${price:.2f} 단품 — 배대지 배송비 대비 실익 적음"
    strong_code = bool(_g(d, "coupon_code")) and _g(d, "code_kind") == "public"
    rising = fe.sd_rising(d) if not isinstance(d, dict) else ""
    # 슬릭딜 Hot Deals 급상승 딜은 할인율을 몰라도·문턱이 낮아도 보낸다(커뮤니티가 실시간 검증 중)
    if not pct and not strong_code and not rising:
        return False, "할인율 모름"
    floor = TECH_MIN if cat == "electronics" else (ETC_MIN if cat == "etc" else 0)
    if pct and pct < floor and not rising:
        return False, f"할인 {pct:.0f}% < {floor:.0f}%"
    ok, why = brand_ok(d)
    if not ok:
        return False, why
    kr, krw = _g(d, "kr_lowest_price"), _g(d, "krw_effective")
    if kr and krw:
        saving = (1 - krw / kr) * 100
        if saving < KR_SAVING_MIN:
            return False, f"국내가 대비 {saving:.0f}% 절감 < {KR_SAVING_MIN:.0f}%"
    else:
        limit = 150 if WIRELESS.search(f"{_g(d, 'brand') or ''} {title}") else 200
        if price > limit and score < 85:
            return False, f"면세한도 ${limit} 초과 + 국내가 모름"
    return True, ""


def select(deals: list) -> list:
    """알림 보낼 딜만 점수순으로, 한 번에 MAX_PER_RUN 개까지."""
    keep, why = [], {}
    for d in deals:
        ok, r = worth_alert(d)
        if ok:
            keep.append(d)
        else:
            k = re.sub(r"[\d$.,%]+", "", r).split("(")[0].replace("<", "").strip()
            why[k] = why.get(k, 0) + 1
    keep.sort(key=lambda d: -(_g(d, "score") or 0))
    per, capped = {}, []
    for d in keep:
        b = _norm(_g(d, "brand") or brand_ok(d)[1])
        per[b] = per.get(b, 0) + 1
        if per[b] <= PER_BRAND:
            capped.append(d)
    if len(capped) < len(keep):
        print(f"[알림 게이트] 같은 브랜드 {len(keep) - len(capped)}건은 다음 실행으로 미룸")
    keep = capped
    if why:
        print("[알림 게이트] 제외:", ", ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda x: -x[1])))
    if len(keep) > MAX_PER_RUN:
        print(f"[알림 게이트] {len(keep)}건 중 상위 {MAX_PER_RUN}건만 알림")
    return keep[:MAX_PER_RUN]
