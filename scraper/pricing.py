"""
pricing.py — 환율 정규화 · 관부가세 · 국내 최저가

세 가지를 담당한다:

1) **환율 정규화** — eBay는 접속 지역 때문에 KRW를, 나머지는 USD를 준다.
   통화가 섞이면 점수 비교가 어긋나므로 모든 가격을 원화로 환산해 비교한다.

2) **관부가세 계산** — 해외직구는 표시가격이 최종가가 아니다.
   목록가가 싸 보여도 관세·부가세를 더하면 국내가와 역전되는 경우가 많다.
   스펙 H2가 판정하려는 게 바로 이 지점이다.

3) **국내 최저가 조회** — 네이버 쇼핑 검색 API로 같은 상품의 국내 최저가를 가져온다.
   `krw_effective`(실질 원화가) vs `kr_lowest_price`(국내 최저가)를 비교해
   실질 절감이 5% 미만이면 H2로 탈락시킨다.

환경변수:
    NAVER_CLIENT_ID / NAVER_CLIENT_SECRET   (네이버 개발자센터, 무료·일 25,000회)
없으면 국내가 조회는 건너뛴다(H2는 비활성, 나머지는 정상 동작).
"""
from __future__ import annotations
import os, json, re, time, urllib.request, urllib.parse, urllib.error

UA = "HotdealRadar/0.1"

# ---------------------------------------------------------------------------
# 1. 환율
# ---------------------------------------------------------------------------
FX_SOURCES = [
    ("https://open.er-api.com/v6/latest/USD", lambda d: d["rates"]["KRW"]),
    ("https://api.frankfurter.app/latest?from=USD&to=KRW", lambda d: d["rates"]["KRW"]),
]
FX_FALLBACK = 1400.0          # 두 소스 모두 실패 시 보수적 기본값
_fx_cache: dict = {"rate": None, "at": 0.0}
FX_TTL_SEC = 6 * 3600         # 6시간 캐시 (환율은 자주 안 변한다)


def usd_krw(force: bool = False) -> float:
    if not force and _fx_cache["rate"] and time.time() - _fx_cache["at"] < FX_TTL_SEC:
        return _fx_cache["rate"]
    for url, pick in FX_SOURCES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8) as r:
                rate = float(pick(json.loads(r.read())))
            if 500 < rate < 3000:          # 상식 범위 검증
                _fx_cache.update(rate=rate, at=time.time())
                return rate
        except Exception:
            continue
    print(f"[pricing] 환율 조회 실패 → 기본값 {FX_FALLBACK} 사용")
    return FX_FALLBACK


def to_krw(amount: float | None, currency: str) -> float | None:
    if amount is None:
        return None
    return amount if (currency or "USD").upper() == "KRW" else amount * usd_krw()


# ---------------------------------------------------------------------------
# 2. 관부가세 (한국 기준, 일반 통관)
# ---------------------------------------------------------------------------
# 미국発 특송은 한미FTA로 목록통관 면세한도가 USD 200, 그 외 국가는 USD 150.
# 한도를 넘으면 '전체 금액'에 과세된다(초과분만이 아니다) — 직구에서 자주 헷갈리는 지점.
DUTY_FREE_USD = {"US": 200.0, "DEFAULT": 150.0}
VAT_RATE = 0.10
# 품목별 관세율(대표값). 정확한 세율은 HS코드에 따르므로 근사치로 쓴다.
DUTY_RATES = {
    "premium_fashion": 0.13,   # 의류
    "sports_outdoor": 0.13,
    "kids": 0.13,
    "watch_misc": 0.08,
    "electronics": 0.00,       # 대부분 무관세(부가세만)
    "etc": 0.08,
}


def landed_cost_krw(price_usd: float, category: str = "etc",
                    shipping_usd: float = 0.0, origin: str = "US") -> float:
    """
    관부가세·배송비를 포함한 실질 원화가.
    면세한도 이하면 세금 없이 (상품가+배송비)만.
    """
    fx = usd_krw()
    goods = price_usd + shipping_usd
    limit = DUTY_FREE_USD.get(origin, DUTY_FREE_USD["DEFAULT"])
    if goods <= limit:
        return round(goods * fx)
    duty_rate = DUTY_RATES.get(category, DUTY_RATES["etc"])
    duty = goods * duty_rate
    vat = (goods + duty) * VAT_RATE
    return round((goods + duty + vat) * fx)


# ---------------------------------------------------------------------------
# 3. 국내 최저가 (네이버 쇼핑 검색 API)
# ---------------------------------------------------------------------------
NAVER_API = "https://openapi.naver.com/v1/search/shop.json"
_kr_cache: dict = {}
_NAVER_DELAY = 0.25   # 초당 4회 이하로 제한

_TAG = re.compile(r"<[^>]+>")
_NOISE = re.compile(
    r"\b(\d+\s*(pack|ct|pk)|men'?s|women'?s|kids?|little kid|big kid|toddler|infant"
    r"|grade a|refurbished|new|sale|clearance|free shipping)\b", re.I)


def search_query(brand: str, title: str) -> str:
    """국내 검색용 질의어를 만든다. 사이즈·성별·수량 같은 노이즈를 걷어낸다."""
    t = _NOISE.sub(" ", title or "")
    t = re.sub(r"[\(\)\[\]{},/|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = t.split()[:6]
    q = " ".join(words)
    if brand and brand.lower() not in q.lower():
        q = f"{brand} {q}"
    return q.strip()[:80]


def kr_lowest_price(brand: str, title: str, display: int = 10) -> float | None:
    """
    네이버 쇼핑에서 국내 최저가(원). 못 찾으면 None.
    None이면 H2 판정을 건너뛴다 — 확신 없이 탈락시키지 않는다.
    """
    cid = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and secret):
        return None

    q = search_query(brand, title)
    if len(q) < 3:
        return None
    if q in _kr_cache:
        return _kr_cache[q]

    url = f"{NAVER_API}?{urllib.parse.urlencode({'query': q, 'display': display, 'sort': 'sim'})}"
    # 네이버 API는 짧은 시간에 몰아치면 429/HTTPError 를 낸다(실측: 25건 중 7건 실패).
    # 요청 사이에 간격을 둬 안정성을 높인다.
    time.sleep(_NAVER_DELAY)
    try:
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": cid,
            "X-Naver-Client-Secret": secret,
            "User-Agent": UA,
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            items = json.loads(r.read()).get("items", [])
    except urllib.error.HTTPError as e:
        # 429(호출 초과)면 잠시 쉬고 한 번만 재시도
        if e.code == 429:
            time.sleep(1.0)
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    items = json.loads(r.read()).get("items", [])
            except Exception:
                print(f"[pricing] 네이버 호출 초과 q={q[:40]}")
                return None
        else:
            print(f"[pricing] 네이버 조회 실패(HTTP {e.code}) q={q[:40]}")
            return None
    except Exception as e:
        print(f"[pricing] 네이버 조회 실패({type(e).__name__}) q={q[:40]}")
        return None

    prices = []
    for it in items:
        try:
            p = float(it.get("lprice") or 0)
        except (TypeError, ValueError):
            continue
        if p > 0:
            prices.append(p)

    # 최저가 하나만 쓰면 짝퉁·부품·중고 같은 이상치에 걸린다.
    # 하위값들의 중앙값 쪽을 쓰는 게 안전하다.
    if not prices:
        _kr_cache[q] = None
        return None
    prices.sort()
    use = prices[: max(3, len(prices) // 2)]
    val = use[len(use) // 2]
    _kr_cache[q] = val
    return val


# ---------------------------------------------------------------------------
# 딜에 적용
# ---------------------------------------------------------------------------
def enrich(deals: list, lookup_kr: bool = True, kr_limit: int = 25) -> list:
    """
    각 딜에 krw_effective(관부가세 포함 실질 원화가)와
    kr_lowest_price(국내 최저가)를 채운다 → H2 하드필터가 실제로 작동하게 된다.

    국내가 조회는 API 쿼터를 쓰므로 점수 상위 딜에만 적용한다(kr_limit).
    """
    fx = usd_krw()
    print(f"[pricing] 환율 USD/KRW = {fx:,.1f}")

    for d in deals:
        cur = getattr(d, "currency", "USD") or "USD"
        if d.price_current is None:
            continue
        if cur.upper() == "KRW":
            # 이미 원화로 노출된 가격(eBay 한국 접속)은 세금 포함가로 본다
            d.krw_effective = d.price_current
        else:
            d.krw_effective = landed_cost_krw(d.price_current, d.category or "etc")

    if not lookup_kr:
        return deals

    # 점수 높은 순으로 제한된 수만 국내가 조회
    ranked = sorted([d for d in deals if d.price_current],
                    key=lambda x: -(x.discount_pct or 0))[:kr_limit]
    hit = 0
    for d in ranked:
        kr = kr_lowest_price(d.brand, d.title)
        if kr:
            d.kr_lowest_price = kr
            hit += 1
    if hit:
        print(f"[pricing] 국내 최저가 조회 {hit}/{len(ranked)}건 매칭")
    return deals
