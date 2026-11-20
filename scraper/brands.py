"""
brands.py — 브랜드 가치 판정

해외직구 의류·잡화는 "싸고 할인율만 높은" 물건이 좋은 딜이 아니다.
브랜드 가치가 먼저 선행돼야 한다(무명 브랜드 90% 할인 < 유명 브랜드 60% 할인).
반면 전자제품은 브랜드 무관 — 가격·할인·활용도로만 본다.

이 모듈은 사용자의 4년치 카카오톡 공유 이력에서 실제로 등장한 브랜드를
근거로 만든 사전(data/brands.csv)을 로드해, 임의의 brand 문자열을
    premium / known / unknown
세 단계로 판정한다.

  - premium : 명품·디자이너·고가 컨템포러리 (점수 크게 가산, 최우선 노출)
  - known   : 대중적으로 인지도 있는 정품 브랜드 (통과, 소폭 가산)
  - unknown : 사전에 없는 무명 브랜드 (패션/잡화면 초고할인일 때만 통과)

사전은 CSV라 사용자가 직접 열어 브랜드를 추가/삭제/승격할 수 있다.
"""
from __future__ import annotations
import csv
import os
import re

_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "brands.csv")

# (match_term, tier, canonical) 목록. 긴 term 우선 매칭을 위해 길이 내림차순 정렬해 둔다.
_TERMS: list[tuple[str, str, str]] = []
_loaded = False


def _norm(s: str) -> str:
    """소문자화 + 공백 정규화. 매칭 정확도를 위한 최소 처리."""
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tier = (row.get("tier") or "known").strip()
                canon = (row.get("canonical") or "").strip()
                for term in (row.get("match_terms") or "").split("|"):
                    term = _norm(term)
                    if term:
                        _TERMS.append((term, tier, canon))
    except FileNotFoundError:
        pass
    # 긴 별칭이 먼저 매칭되도록(예: "polo ralph"가 "polo"보다 우선)
    _TERMS.sort(key=lambda t: len(t[0]), reverse=True)


def lookup(brand: str) -> tuple[str, str]:
    """
    brand 문자열 → (tier, canonical).
    사전에 없으면 ("unknown", "").
    부분 문자열 매칭: "adidas Originals" → adidas(known),
    "Kate Spade New York" → Kate Spade(premium).
    """
    _load()
    b = _norm(brand)
    if not b:
        return ("unknown", "")
    for term, tier, canon in _TERMS:
        # 단어 경계를 존중해 오탐을 줄인다(예: 'on'이 'wilson'에 걸리지 않게).
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", b):
            return (tier, canon)
    return ("unknown", "")


def tier(brand: str) -> str:
    return lookup(brand)[0]


def is_valued(brand: str) -> bool:
    """사전에 있는(premium 또는 known) 브랜드인가."""
    return tier(brand) != "unknown"


# 필터/스코어가 바로 쓰는 상수
TIER_BONUS = {"premium": 12, "known": 6, "unknown": 0}

# 브랜드 게이트를 적용할 카테고리(패션·잡화·시계).
# 전자제품(electronics)과 기타(etc)는 브랜드 무관 — 게이트 예외.
BRAND_GATED_CATEGORIES = {"premium_fashion", "sports_outdoor", "kids", "watch_misc"}

# 무명 브랜드라도 통과시키는 '초고할인' 기준(%).
UNKNOWN_BRAND_MIN_DISCOUNT = float(os.environ.get("RADAR_UNKNOWN_BRAND_MIN_DISCOUNT", "85"))


# ---------------------------------------------------------------------------
# 테크·생활·게임 브랜드 (패션 브랜드는 data/brands.csv). 전자제품 등 '브랜드 무관' 카테고리에서
# 브랜드가 확실한지(알림 게이트), 알려진 브랜드 가산점(스코어링)에 쓴다.
# 2026-11-20: Bexel 단톡방 공유 슬릭딜 252건에서 반복된 브랜드 추가(게이밍·오디오·조명·카메라 가방 등).
# ---------------------------------------------------------------------------
TECH_BRANDS = [
    "apple", "airpods", "ipad", "macbook", "samsung", "galaxy", "sony", "bose", "beats", "jbl", "anker",
    "soundcore", "eufy", "garmin", "logitech", "dyson", "nintendo", "playstation", "xbox", "lg", "dell",
    "lenovo", "hp", "asus", "acer", "microsoft", "google", "pixel", "kindle", "amazon echo", "echo",
    "fire tv", "gopro", "dji", "canon", "nikon", "fujifilm", "sandisk", "western digital", "wd",
    "seagate", "crucial", "kingston", "razer", "steelseries", "corsair", "philips", "sonicare", "braun",
    "panasonic", "oral-b", "shark", "ninja", "instant pot", "kitchenaid", "le creuset", "staub", "zwilling",
    "yeti", "stanley", "hydro flask", "theragun", "therabody", "oura", "fitbit", "sennheiser",
    "audio-technica", "marshall", "sonos", "ugreen", "belkin", "roborock", "irobot", "roomba", "tp-link",
    "netgear", "nespresso", "breville", "keurig", "vitamix", "cuisinart", "lodge", "ember", "whoop",
    "coros", "suunto", "polar", "bowers & wilkins", "bang & olufsen", "shokz", "nothing", "osprey",
    "mountain hardwear", "arc'teryx", "patagonia", "the north face", "rei co-op",
    # 2026-11-20 추가
    "hyperx", "turtle beach", "powera", "8bitdo", "thrustmaster", "elgato", "blue yeti", "rode", "shure",
    "skullcandy", "jabra", "jlab", "edifier", "klipsch", "energy speaker", "polk", "denon", "yamaha",
    "govee", "nanoleaf", "philips hue", "wyze", "ring", "arlo", "blink", "ecovacs", "tineco", "levoit",
    "peak design", "pelican", "samsonite", "tumi", "victorinox", "swissgear", "leatherman", "benchmade",
    "zojirushi", "fellow", "timemore", "hario", "baratza", "de'longhi", "delonghi", "smeg", "ooni",
    "msi", "gigabyte", "nvidia", "amd", "intel", "viewsonic", "benq", "aoc", "alienware", "framework",
    "kodak", "insta360", "ricoh", "olympus", "om system", "sigma", "tamron", "manfrotto",
    "frameo", "aura frame", "brother", "epson", "remarkable", "boox", "kobo",
    "harman kardon", "keychron", "technics", "atomos", "kensington", "energy", "audioengine", "fender",
    "oura", "garmin", "nanoleaf", "coleman",
]
_TECH = sorted(TECH_BRANDS, key=len, reverse=True)


# 일반 단어와 겹치는 이름은 문자열 맨 앞에 올 때만 브랜드로 본다(예: 'Ring Light', 'polar fleece')
_GENERIC = {"ring", "blink", "fellow", "framework", "polar", "nothing", "echo", "shark", "ninja", "stanley",
            "lodge", "rode", "sigma", "galaxy", "ember", "whoop", "pixel", "google", "apple", "lg", "hp", "wd",
            "polk", "fire tv", "kindle", "amd", "intel", "brother", "aura frame", "energy speaker", "energy"}


def tech_brand(text: str) -> str:
    """제목·브랜드 문자열 안에 알려진 테크·생활 브랜드가 있으면 그 이름."""
    t = _norm(text)
    t = re.sub(r"^(\[[^\]]*\]\s*|\(?(new|used|open box|certified)?\s*refurb(ished)?\)?:?\s*|prime( members?)?:?\s*|"
               r"\$[\d.,]+\*?\s*[:|]\s*|\d+(\.\d+)?\s*(mm|l|\"|in|oz|gb|tb|-?pc|-?piece|-?pack|-?pk|-?ct)\b[\s,-]*)+", "", t)
    for b in _TECH:
        pat = rf"(?<![a-z]){re.escape(b)}(?![a-z])"
        if (re.match(pat, t) if b in _GENERIC else re.search(pat, t)):
            return b
    return ""
