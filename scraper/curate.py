"""
curate.py — 딜목록 큐레이션 (중복·도배 정리)

진단에서 드러난 문제:
  - 무명 브랜드 Adornia가 10건, 그중 5건이 똑같이 $19.98(색상만 다른 같은 상품).
  - 같은 상품의 색상/사이즈 변형이 슬롯을 여러 개 잡아먹어 리스트가 지저분해진다.

두 단계로 정리한다.
  1) **변형 묶기**: 같은 브랜드 + (색상·사이즈·수량 노이즈를 제거한) 같은 제목이면
     한 상품으로 보고, 할인 깊은 것 하나만 남긴다. "외 N색"으로 몇 개였는지 표시.
  2) **브랜드 도배 상한**: 한 브랜드가 리스트를 도배하지 못하게 티어별 상한을 둔다.
     무명 브랜드는 특히 빡세게(기본 1건) — 초고할인 예외로 뚫고 들어와도 도배는 막는다.

딕셔너리(asdict된 딜) 리스트를 입력받아 정리된 리스트를 돌려준다.
"""
from __future__ import annotations
import os
import re

# 제목에서 걷어낼 노이즈 — 색상·사이즈·수량·성별 등 '같은 상품의 변형' 표지
_NOISE = re.compile(
    r"\b("
    r"black|white|blue|red|green|gold|silver|grey|gray|brown|navy|pink|purple|"
    r"beige|tan|ivory|cream|olive|khaki|burgundy|teal|coral|mint|lilac|"
    r"little kid|big kid|toddler|infant|men'?s|women'?s|unisex|youth|"
    r"set of \d+|\d+[- ]?pack|\d+[- ]?pk|\d+ct|pack of \d+|"
    r"small|medium|large|x-?large|xl|xs|xxl|size \d+"
    r")\b", re.I)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# 티어별 브랜드 노출 상한 (환경변수로 조정 가능)
BRAND_CAP = {
    "premium": int(os.environ.get("RADAR_CAP_PREMIUM", "5")),
    "known": int(os.environ.get("RADAR_CAP_KNOWN", "4")),
    "unknown": int(os.environ.get("RADAR_CAP_UNKNOWN", "1")),
    "": int(os.environ.get("RADAR_CAP_ETC", "6")),   # 전자제품 등(브랜드 무관)
}


def _norm_title(brand: str, title: str) -> str:
    """변형 노이즈를 제거한 상품 핵심 이름. 같은 상품이면 같은 문자열이 되도록."""
    t = (title or "").lower()
    t = _NOISE.sub(" ", t)
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    words = t.split()[:6]           # 앞 6단어면 상품 동일성 판단에 충분
    return f"{(brand or '').lower()}|{' '.join(words)}"


def _disc(d: dict) -> float:
    return d.get("discount_pct") or 0


def collapse_variants(deals: list[dict]) -> list[dict]:
    """색상/사이즈 변형을 묶어 대표 1건만 남긴다(할인 깊은 것). '외 N색' 표시."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for d in deals:
        sig = _norm_title(d.get("brand", ""), d.get("title", ""))
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(d)

    out = []
    for sig in order:
        g = groups[sig]
        best = max(g, key=lambda x: (_disc(x), x.get("score", 0)))
        extra = len(g) - 1
        if extra > 0:
            best = dict(best)
            best["variant_count"] = len(g)
            # 제목 끝에 변형 개수 표기(중복 방지)
            if "외 " not in best.get("title", ""):
                best["title"] = f'{best.get("title","")}  (외 {extra}종)'
        out.append(best)
    return out


def cap_per_brand(deals: list[dict], caps: dict | None = None) -> list[dict]:
    """한 브랜드가 도배하지 못하게 티어별 상한 적용. 점수 높은 것부터 남긴다."""
    caps = caps or BRAND_CAP
    ranked = sorted(deals, key=lambda x: x.get("score", 0), reverse=True)
    seen: dict[str, int] = {}
    out = []
    for d in ranked:
        brand = (d.get("brand") or "").strip().lower() or "?"
        tier = d.get("brand_tier", "")
        cap = caps.get(tier, caps.get("", 6))
        n = seen.get(brand, 0)
        if n >= cap:
            continue
        seen[brand] = n + 1
        out.append(d)
    return out


def curate(deals: list[dict]) -> list[dict]:
    """변형 묶기 → 브랜드 도배 상한. 딜보드/알림 공통 진입점."""
    collapsed = collapse_variants(deals)
    capped = cap_per_brand(collapsed)
    return capped


if __name__ == "__main__":
    import json, collections
    path = os.path.join(os.path.dirname(__file__), "..", "web", "deals.json")
    deals = json.load(open(path, encoding="utf-8"))["deals"]
    after = curate(deals)
    print(f"큐레이션: {len(deals)} → {len(after)}건")
    bc0 = collections.Counter(x.get("brand") for x in deals)
    bc1 = collections.Counter(x.get("brand") for x in after)
    print("Adornia:", bc0.get("Adornia"), "→", bc1.get("Adornia"))
    print("변형 묶인 딜:", sum(1 for x in after if x.get("variant_count")))
