"""
filter_engine.py
DEAL_FILTER_SPEC.md 의 2~4단계(하드필터 / 스코어링 / 긴급도)를 코드로 옮긴 것.
소스 어댑터가 만들어 준 Deal 객체를 받아 통과/점수/등급을 매긴다.

스펙 변경 시 여기 상수만 고치면 파이프라인 전체에 반영된다.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import os
import re

import brands


# ---------------------------------------------------------------------------
# 데이터 모델 — 스펙 "구현 시 데이터 요구사항" 체크리스트와 1:1 대응
# ---------------------------------------------------------------------------
@dataclass
class Deal:
    source: str                       # 예: "dealsofamerica", "slickdeals_api", "nordstromrack"
    source_tier: str                  # "T1" / "T2" / "T3"
    url: str
    title: str
    brand: str = ""
    category: str = ""                # 스펙 3-C 카테고리 라벨
    image: str = ""                   # 상품 썸네일 URL (딜보드 표시용)

    price_current: Optional[float] = None
    price_list: Optional[float] = None       # 리테일러 표기 정가
    price_baseline: Optional[float] = None    # 가격 히스토리 평시가 (없으면 price_list 대체)
    price_alltime_low: Optional[float] = None
    # 사이트별 표시 통화. eBay는 한국 접속 시 KRW로 준다.
    # 할인율은 같은 통화끼리 계산하므로 영향 없지만,
    # H2(국내가 역전) 판정은 원화로 환산해야 하므로 이 값이 필요하다.
    currency: str = "USD"

    coupon_code: str = ""
    coupon_stackable: bool = False
    off_season: bool = False          # 역시즌 여부
    free_gift: bool = False           # 사은품/증정

    community_votes: Optional[int] = None     # Slickdeals
    rating: Optional[float] = None            # Amazon 등 평점
    review_count: Optional[int] = None

    ships_to_kr: bool = True
    forwarding_ok: bool = True        # 배대지 가능 여부
    krw_effective: Optional[float] = None     # 관부가세+배송비 포함 실질 원화가
    kr_lowest_price: Optional[float] = None   # 국내 최저가 (H2 판정용)

    stock_status: str = "in_stock"    # in_stock / low / out_of_stock
    is_refurbished: bool = False
    expires_at: Optional[str] = None

    collection_method: str = "scrape"  # api / rss / scrape / manual_input
    frontpage: bool = False            # Slickdeals Frontpage 진입 여부
    # 이 딜의 근거가 된 코드의 성격 (public / welcome / personal / unknown)
    code_kind: str = ""

    # 파이프라인이 채우는 필드
    brand_tier: str = ""               # premium / known / unknown (스코어링이 채움)
    discount_pct: Optional[float] = None
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)
    passed_hard_filter: bool = False
    reject_reason: str = ""
    urgency: str = ""                  # FLASH / HOT / STEADY / (미발행)
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# 할인율 계산 — 스펙: 평시가(price_baseline) 대비. 없으면 표기정가로 폴백.
# ---------------------------------------------------------------------------
def compute_discount_pct(deal: Deal) -> Optional[float]:
    base = deal.price_baseline or deal.price_list
    if not base or not deal.price_current or base <= 0:
        return None
    pct = (1 - deal.price_current / base) * 100
    return round(max(pct, 0.0), 1)


# ---------------------------------------------------------------------------
# 2단계. 하드필터 — 하나라도 걸리면 탈락 (H1~H5)
# ---------------------------------------------------------------------------
def hard_filter(deal: Deal) -> tuple[bool, str]:
    # H1: 한국 직배송 불가 + 배대지 불가
    if not deal.ships_to_kr and not deal.forwarding_ok:
        return False, "H1: 한국 직배송/배대지 모두 불가"

    # H2: 관부가세 포함 국내가 역전 (국내 최저가 대비 5% 미만 절감)
    if deal.krw_effective and deal.kr_lowest_price:
        saving = 1 - (deal.krw_effective / deal.kr_lowest_price)
        if saving < 0.05:
            return False, f"H2: 국내가 대비 실질 절감 {saving*100:.1f}% (<5%)"

    # H3: 노브랜드/무명 셀러
    if deal.community_votes is not None and deal.community_votes < 0:
        return False, "H3: Slickdeals 커뮤니티 투표 음수"
    if deal.rating is not None and deal.rating < 4.0:
        return False, f"H3: 평점 {deal.rating} < 4.0"
    if deal.review_count is not None and deal.review_count < 50:
        return False, f"H3: 리뷰 {deal.review_count}개 < 50"

    # H4: 만료/품절
    if deal.stock_status == "out_of_stock":
        return False, "H4: 품절"
    if deal.expires_at:
        try:
            exp = datetime.fromisoformat(deal.expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                return False, "H4: 만료됨"
        except ValueError:
            pass

    # H5: 리퍼/중고 (단, 리퍼 명시 + 60% 이상 할인은 예외 통과)
    if deal.is_refurbished:
        pct = deal.discount_pct if deal.discount_pct is not None else compute_discount_pct(deal)
        if not (pct and pct >= 60):
            return False, "H5: 리퍼/중고 (60% 미만 할인)"

    # H6: 신규가입·개인 전용 코드에 기반한 딜은 발행하지 않는다.
    #
    # 실측에서 발견: 아웃넷 환영 메일이 "70% 세일" 문구를 품고 있어
    # 코드를 제외했는데도 70% 딜로 발행됐다.
    # 환영 메일의 할인 문구는 가입 유도용이라 실제 진행 중인 세일이라는 보장이 없고,
    # 제목도 딜 내용을 설명하지 않는다("...에 오신 것을 환영합니다").
    # 진짜 세일이면 별도 프로모션 메일이 따로 온다.
    if deal.code_kind in ("welcome", "personal"):
        return False, f"H6: {'신규가입' if deal.code_kind == 'welcome' else '개인'} 전용 (공유 불가)"

    # H7: 브랜드 가치 게이트 (의류·잡화·시계 한정).
    #
    # 해외직구 패션·잡화는 "싸고 할인율만 높은" 물건이 좋은 딜이 아니다.
    # 무명 브랜드는 90% 할인이어도 살 이유가 없다 — 브랜드 가치가 먼저다.
    # 전자제품(electronics)·기타(etc)는 활용도로 사는 것이라 이 게이트를 적용하지 않는다.
    # 단, 무명 브랜드라도 초고할인(기본 85%+)이면 예외적으로 통과시킨다.
    #
    # 추가 예외: 아마존식 '결제창 코드 딜'(공개 코드)은 브랜드 게이트에서 뺀다.
    # 이런 딜의 가치는 브랜드가 아니라 '지금 이 코드로 추가 할인이 실제로 적용된다'는
    # 행동가능성에 있다. 사용자 요청으로 무명이어도 통과시킨다.
    if deal.category in brands.BRAND_GATED_CATEGORIES and not has_public_code(deal):
        if brands.tier(deal.brand) == "unknown":
            pct = deal.discount_pct if deal.discount_pct is not None else compute_discount_pct(deal)
            if not (pct and pct >= brands.UNKNOWN_BRAND_MIN_DISCOUNT):
                return False, (f"H7: 무명 브랜드({deal.brand or '미상'}) — "
                               f"할인 {pct or 0:.0f}% < {brands.UNKNOWN_BRAND_MIN_DISCOUNT:.0f}%")

    return True, ""


def has_public_code(deal: Deal) -> bool:
    """공유 가능한(공개) 결제창 프로모션 코드가 붙은 딜인가."""
    return bool(getattr(deal, "coupon_code", "") and deal.code_kind == "public")


# ---------------------------------------------------------------------------
# 3단계. 스코어링 (100점 만점)
# ---------------------------------------------------------------------------

# 3-C 카테고리 가중치 — 브랜드 키워드로 카테고리 자동 추정할 때도 사용
CATEGORY_SCORES = {
    "premium_fashion": 20,   # Polo RL, Burberry, Theory, Tommy, UGG, North Face ...
    "sports_outdoor": 17,    # Nike, Adidas, NB, Columbia, Oakley, REI ...
    "electronics": 15,
    "kids": 15,
    "watch_misc": 10,
    "etc": 5,
}

CATEGORY_KEYWORDS = {
    # 프리미엄 패션(20점) — 4년 실측 최다 카테고리 + 리테일러에서 실제 관측된 브랜드
    "premium_fashion": [
        "polo", "ralph lauren", "burberry", "theory", "tommy", "ugg",
        "north face", "the row", "maison margiela", "margiela", "lemaire", "max mara",
        "thom browne", "zadig", "kate spade", "michael kors", "coach", "marc jacobs",
        "vince camuto", "versace", "versus versace", "steve madden", "sam edelman",
        "cole haan", "calvin klein", "hugo boss", "lacoste", "diesel", "dkny",
        "club monaco", "j.crew", "jcrew", "madewell", "banana republic", "everlane",
        "acne studios", "ganni", "sandro", "maje", "reiss", "allsaints", "ted baker",
        "brooks brothers", "eileen fisher", "vince", "rag & bone", "rag-bone",
        "helmut lang", "alexander wang", "self-portrait", "stuart weitzman",
        "jimmy choo", "salvatore ferragamo", "ferragamo", "tory burch", "longchamp",
        "furla", "mulberry", "bally", "paul smith", "aquatalia", "naot", "ecco",
        "clarks", "birkenstock", "dr. martens", "frye", "loeffler randall",
        # 실측에서 '기타'로 잘못 분류돼 탈락했던 브랜드들 (2026-07 보완)
        "free people", "7 for all mankind", "blanknyc", "boss", "alexia admor",
        "mia", "vince", "joe's jeans", "paige", "ag jeans", "citizens of humanity",
        "lucky brand", "levi's", "wrangler", "guess", "bcbg", "french connection",
        "karen kane", "nic+zoe", "chaus", "tahari", "adrianna papell", "eliza j",
        "dkny", "anne klein", "jones new york", "nine west", "naturalizer",
        "franco sarto", "lucky", "sanctuary", "bobeau", "gibsonlook", "wit & wisdom",
        "kut from the kloth", "democracy", "hudson", "j brand", "rag and bone",
        "splendid", "velvet", "michael stars", "three dots", "z supply",
    ],
    # 스포츠/아웃도어(17점)
    "sports_outdoor": [
        "nike", "adidas", "new balance", "columbia", "oakley", "rei", "asics",
        "on cloud", "on running", "arc'teryx", "arcteryx", "reebok", "hoka",
        "under armour", "puma", "brooks", "saucony", "salomon", "merrell",
        "patagonia", "the north face", "marmot", "mammut", "keen", "teva",
        "skechers", "converse", "vans", "new era", "champion", "fila",
        "lululemon", "athleta", "gymshark", "carhartt", "timberland", "danner",
        "smartwool", "darn tough", "osprey", "black diamond", "garmin",
    ],
    # 전자기기(15점)
    "electronics": [
        "ssd", "earbuds", "headphone", "headphones", "tablet", "ipad", "monitor",
        "laptop", "gpu", "nvme", "airpods", "samsung", "sony", "bose", "anker",
        "logitech", "lenovo", "dell", "hp ", "asus", "acer", "seagate", "crucial",
        "sandisk", "western digital", "jbl", "harman kardon", "beats", "roku",
        "kindle", "echo dot", "nintendo", "playstation", "xbox", "webcam",
        "keyboard", "mouse", "router", "soundbar", "projector",
        # 한국어 — eBay 등은 접속 지역에 따라 한국어 제목을 준다
        "다이슨", "삼성", "엘지", "소니", "보스", "앤커", "로지텍", "레노버",
        "이어폰", "헤드폰", "무선이어폰", "태블릿", "노트북", "모니터", "충전기",
        "보안 카메라", "카메라", "청소기", "에어컨", "공기청정기", "티비", "tv 스틱",
        "스피커", "키보드", "마우스", "공유기", "계산기", "그래핑",
        # 상품 유형 키워드 — 아마존처럼 제목이 서술형인 사이트 대응.
        # 브랜드 사전만으로는 신규·무명 브랜드를 계속 놓친다.
        "gaming monitor", "robot vacuum", "air purifier", "power station",
        "solar generator", "dash cam", "smart watch", "smartwatch",
        "bluetooth", "wireless charger", "power bank", "ssd drive",
        "mechanical keyboard", "gaming mouse", "microphone", "streaming",
        "security camera", "video doorbell", "smart bulb", "smart plug",
        "vacuum cleaner", "air fryer", "espresso machine", "blender",
        "electric toothbrush", "hair dryer", "flosser", "massager",
        "humidifier", "dehumidifier", "space heater", "tower fan",
    ],
    # 유아/키즈(15점) — 닉네임이 '유아용품'일 만큼 지속 관심
    "kids": [
        "kids", "boys", "girls", "baby", "toddler", "junior", "little kid",
        "big kid", "infant", "youth", "gymboree", "carter's", "hanna andersson",
        "childrens place", "children's place", "stroller", "car seat", "diaper",
        "pottery barn kids", "melissa & doug", "lego", "playmobil",
        # 한국어
        "유아", "아기", "어린이", "키즈", "아동", "주니어", "유모차", "카시트", "기저귀",
        "레고", "장난감", "피규어", "미니피규어", "완구",
    ],
    # 시계/잡화(10점)
    "watch_misc": [
        "jomashop", "seiko", "citizen", "watch", "tissot", "casio", "g-shock",
        "fossil", "movado", "bulova", "hamilton", "orient", "swarovski", "pandora",
        "adornia", "sunglasses", "ray-ban", "rayban", "maui jim", "samsonite",
        "tumi", "luggage", "backpack",
    ],
}


# 리테일러별 기본 카테고리.
#
# 브랜드 사전은 아무리 채워도 신상·신진 브랜드를 계속 놓친다.
# 그런데 노드스트롬랙·자포스처럼 '취급 품목이 정해진' 리테일러에서 온 딜은
# 브랜드를 몰라도 카테고리를 상당히 확신할 수 있다.
# 사전에 없는 브랜드가 무조건 '기타 5점'으로 떨어져 좋은 딜이 탈락하던 문제를 막는다.
#
# 단, 키워드로 확인된 것보다는 낮게 본다(아래 SITE_FALLBACK_PENALTY).
SITE_DEFAULT_CATEGORY = {
    "nordstromrack.com": "premium_fashion",
    "nordstrom.com": "premium_fashion",
    "saksoff5th.com": "premium_fashion",
    "saksfifthavenue.com": "premium_fashion",
    "bloomingdales.com": "premium_fashion",
    "macys.com": "premium_fashion",
    "theoutnet.com": "premium_fashion",
    "mytheresa.com": "premium_fashion",
    "ssense.com": "premium_fashion",
    "shopbop.com": "premium_fashion",
    "zappos.com": "sports_outdoor",     # 신발 중심
    "6pm.com": "sports_outdoor",
    "rei.com": "sports_outdoor",
    "backcountry.com": "sports_outdoor",
    "columbia.com": "sports_outdoor",
    "jomashop.com": "watch_misc",
    # 2026-07 재점검으로 새로 크롤 가능해진 사이트들
    "jcrew.com": "premium_fashion",
    "madewell.com": "premium_fashion",
    "ae.com": "premium_fashion",            # American Eagle
    "aeropostale.com": "premium_fashion",
    "arcteryx.com": "sports_outdoor",
    "asics.com": "sports_outdoor",
    "huckberry.com": "sports_outdoor",
    "carhartt.com": "sports_outdoor",       # 워크웨어/아웃도어
    "bestbuy.com": "electronics",
    # 신규로 뚫은 사이트들 (2026-07)
    "shoebacca.com": "sports_outdoor",     # 신발 전문
    "eddiebauer.com": "sports_outdoor",    # 아웃도어 의류
    "endclothing.com": "premium_fashion",  # 스트리트/디자이너 편집숍
    "ssense.com": "premium_fashion",
    "yoox.com": "premium_fashion",       # 명품 아울렛
    "victoriassecret.com": "premium_fashion",
}
# 리테일러 추정은 키워드 확인보다 근거가 약하므로 점수를 조금 깎는다.
SITE_FALLBACK_PENALTY = 4


def infer_category(deal: Deal) -> tuple[str, bool]:
    """(카테고리, 리테일러추정여부) 반환."""
    text = f"{deal.brand} {deal.title}".lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in text for k in kws):
            return cat, False
    fallback = SITE_DEFAULT_CATEGORY.get(deal.source)
    if fallback:
        return fallback, True
    return "etc", False


def score_discount_depth(pct: Optional[float]) -> int:
    if pct is None:
        return 0
    if pct >= 80:
        return 40
    if pct >= 60:
        return 30
    if pct >= 40:
        return 18
    if pct >= 25:
        return 8
    return 0


def score_price_history(deal: Deal) -> int:
    # 역대최저가/1년/90일 판정. 우리가 자체 축적하는 가격 히스토리에 의존.
    # 데이터가 없으면(신규 상품) 0점 + Slickdeals 투표수 폴백(스펙 v2 후보).
    cur = deal.price_current
    if cur is None:
        return 0
    if deal.price_alltime_low is not None:
        if cur <= deal.price_alltime_low:
            return 25          # 역대 최저가 갱신
    # 폴백: 커뮤니티 투표가 아주 높으면 최저가 근접 신호로 대리 사용
    if deal.price_alltime_low is None and deal.community_votes and deal.community_votes >= 50:
        return 10
    return 0


def score_category(deal: Deal) -> tuple[int, str]:
    if deal.category in CATEGORY_SCORES:
        return CATEGORY_SCORES[deal.category], deal.category
    cat, is_fallback = infer_category(deal)
    score = CATEGORY_SCORES.get(cat, 5)
    if is_fallback:
        score = max(5, score - SITE_FALLBACK_PENALTY)
    return score, cat


def score_bonus(deal: Deal) -> int:
    s = 0
    if deal.coupon_stackable:
        s += 8
    if deal.off_season:
        s += 4
    if deal.free_gift:
        s += 3
    return s


def score_brand(deal: Deal) -> tuple[int, str]:
    """
    브랜드 가치 가산점. 패션·잡화·시계에서만 의미가 있다(전자제품은 0).
    premium +12 / known +6 / unknown 0.  유명 브랜드가 상위로 올라오게 하는 축.
    """
    if deal.category not in brands.BRAND_GATED_CATEGORIES:
        return 0, ""
    t = brands.tier(deal.brand)
    return brands.TIER_BONUS.get(t, 0), t


# 공개 코드 딜이 확보하는 점수 하한(STEADY 진입선). 유명 브랜드보다는 아래.
CODE_DEAL_FLOOR = int(os.environ.get("RADAR_CODE_DEAL_FLOOR", "60"))


def has_price_history(deal: Deal) -> bool:
    """B항목을 평가할 근거가 있는가 (자체 가격 이력 DB 축적 여부)."""
    return deal.price_alltime_low is not None


def score_deal(deal: Deal) -> Deal:
    deal.discount_pct = compute_discount_pct(deal)

    # 카테고리를 하드필터보다 먼저 확정한다.
    # H7(브랜드 게이트)이 카테고리를 봐야 하는데, 파서가 준 원본 카테고리는
    # 대개 비어 있어 게이트가 헛돈다(실측: 무명 브랜드가 70%대에도 통과했다).
    c, cat = score_category(deal)
    deal.category = cat

    passed, reason = hard_filter(deal)
    deal.passed_hard_filter = passed
    if not passed:
        deal.reject_reason = reason
        deal.score = 0
        deal.urgency = ""
        return deal

    a = score_discount_depth(deal.discount_pct)
    b = score_price_history(deal)
    d = score_bonus(deal)
    e, btier = score_brand(deal)
    deal.brand_tier = btier

    raw = a + b + c + d + e

    # --- 콜드스타트 보정 ---
    # 가격 이력 DB가 비어있는 초기에는 B항목(25점)을 아무도 못 받아
    # 전 딜이 구조적으로 25점 손해를 본다. A·C·D(+브랜드) 만점을 척도로 정규화한다.
    # 브랜드 게이트 카테고리는 브랜드 12점을 더해 87점, 전자제품 등은 75점 척도.
    # (전자제품은 브랜드 점수를 못 얻으므로 75로 나눠야 불이익이 없다.)
    if not has_price_history(deal):
        denom = 87 if deal.category in brands.BRAND_GATED_CATEGORIES else 75
        deal.score = min(100, round(raw * 100 / denom))
        deal.score_breakdown = {"discount_depth": a, "price_history": None,
                                "category": c, "bonus": d, "brand": e,
                                "note": "가격이력 미축적 → 87점 척도 정규화"}
    else:
        deal.score = min(100, raw)
        deal.score_breakdown = {"discount_depth": a, "price_history": b,
                                "category": c, "bonus": d, "brand": e}

    # 공개 결제창 코드 딜은 브랜드/점수와 무관하게 반드시 노출한다(사용자 요청).
    # 단, 코드 할인이 실질적일 때만(20%+) — 5% 코드 잡음까지 올리지 않는다.
    # 상한을 STEADY 하단(60)으로 둬, 유명 브랜드 정품 딜을 밀어내지 않게 한다.
    if has_public_code(deal) and (deal.discount_pct or 0) >= 20:
        deal.score = max(deal.score, CODE_DEAL_FLOOR)
        deal.score_breakdown["code_deal"] = f"공개코드 {deal.coupon_code} → 하한 {CODE_DEAL_FLOOR}"

    deal.urgency = classify_urgency(deal)
    return deal


# ---------------------------------------------------------------------------
# 4단계. 긴급도 분류
# ---------------------------------------------------------------------------
def classify_urgency(deal: Deal) -> str:
    if deal.score >= 85 or deal.frontpage:
        return "FLASH"      # 즉시 발행 (15분 내)
    if deal.score >= 70:
        return "HOT"        # 다음 정기 슬롯
    if deal.score >= 50:
        return "STEADY"     # 저활동 시간대 채움
    return ""               # 50점 미만 미발행 (로그만)


# ---------------------------------------------------------------------------
# 발행 판정 헬퍼
# ---------------------------------------------------------------------------
def should_publish(deal: Deal) -> bool:
    return deal.passed_hard_filter and deal.score >= 50


def process(deals: list[Deal]) -> list[Deal]:
    return [score_deal(d) for d in deals]
