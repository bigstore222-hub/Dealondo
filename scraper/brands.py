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
