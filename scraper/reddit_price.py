"""
reddit_price.py — 레딧 딜 커뮤니티의 과거 글로 '예전에 얼마까지 내려갔나'를 확인하는 보조 지표

해외직구 핫딜 판정의 보조수단이다(주 판정은 할인율·브랜드·슬릭딜 반응 속도).
  · 같은 상품이 r/buildapcsales, r/deals, r/frugalmalefashion 등에 과거 얼마에 올라왔는지 찾는다
  · 지금 가격이 기록 중 최저 이하면 가점, 기록보다 25% 넘게 비싸면 감점(filter_engine 이 반영)
  · 기록을 못 찾으면 '정보 없음' — 감점하지 않는다

레딧은 서버(클라우드) 접속을 막는 경우가 많아, 공식 앱 인증으로 조회한다.
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (reddit.com/prefs/apps 에서 'script' 앱 무료 생성)
키가 없으면 공개 검색을 한 번 시도하고, 막히면 조용히 건너뛴다(결과는 STATS 에 남김 → 모니터 페이지).
"""
from __future__ import annotations
import base64, json, os, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone

UA = "dealondo-radar/1.0 (deal price history lookup)"
DEAL_SUBS = {"buildapcsales", "deals", "frugalmalefashion", "frugalfemalefashion", "sneakerdeals",
             "gamedeals", "nintendoswitchdeals", "appledeals", "amazondeals", "techdeals",
             "frugal", "deals_reddit", "laptopdeals", "headphonedeals", "dealsreddit", "ps5deals",
             "xboxdeals", "camerasdeals", "outdoorgeardeals", "garmin", "hiphopheads_deals"}
MAX_LOOKUPS = int(os.environ.get("RADAR_REDDIT_MAX", "10"))
STATS: dict = {"mode": "", "queried": 0, "found": 0, "errors": 0, "last_error": "", "at": ""}
_token: dict = {"v": None, "exp": 0.0}
_cache: dict = {}
_off = {"v": False}      # 키 없이 막히면(403/429) 이번 실행에서는 더 묻지 않는다
_NOISE = re.compile(r"\b(men'?s|women'?s|kids?|unisex|new|sale|deal|free shipping|w/|with|and|for|the|"
                    r"\d+(\.\d+)?\s*(oz|ct|pk|pack|count|in|inch|ft|lb|gb|tb)?)\b", re.I)
_PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")


def _auth_token() -> str | None:
    cid = re.sub(r"\s", "", os.environ.get("REDDIT_CLIENT_ID") or "")
    sec = re.sub(r"\s", "", os.environ.get("REDDIT_CLIENT_SECRET") or "")
    if not (cid and sec):
        return None
    if _token["v"] and time.time() < _token["exp"]:
        return _token["v"]
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token", data=b"grant_type=client_credentials",
        headers={"User-Agent": UA,
                 "Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()})
    with urllib.request.urlopen(req, timeout=10) as r:
        j = json.loads(r.read())
    _token.update(v=j.get("access_token"), exp=time.time() + int(j.get("expires_in", 3600)) - 60)
    return _token["v"]


def _search(q: str) -> list[dict]:
    tok = None
    try:
        tok = _auth_token()
    except Exception as e:
        STATS["last_error"] = f"인증 실패 {type(e).__name__}"
    params = urllib.parse.urlencode({"q": q, "sort": "relevance", "t": "all", "limit": 50, "type": "link"})
    if tok:
        STATS["mode"] = "공식 앱 인증"
        url, hdr = f"https://oauth.reddit.com/search?{params}", {"User-Agent": UA, "Authorization": f"bearer {tok}"}
    else:
        STATS["mode"] = "공개 검색(키 없음)"
        url, hdr = f"https://www.reddit.com/search.json?{params}", {"User-Agent": UA}
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=12) as r:
        j = json.loads(r.read())
    return [c.get("data", {}) for c in (j.get("data") or {}).get("children", [])]


def query_for(brand: str, title: str) -> tuple[str, list[str]]:
    """검색어와 '반드시 맞아야 할 단어'(모델 번호 우선)."""
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title or "")
    t = re.sub(r"[^\w\s\-+.]", " ", t)
    if brand and brand.lower() not in t.lower():
        t = f"{brand} {t}"
    words = [w for w in _NOISE.sub(" ", t).split() if len(w) > 1]
    models = [w for w in words if re.search(r"\d", w) and re.search(r"[a-zA-Z]", w)]      # XM5, 990v6, M4 …
    key = (models[:2] or words[1:3]) + ([brand] if brand else [])
    q = " ".join(words[:6])
    return q, [k.lower() for k in key if k]


def lookup(brand: str, title: str, price: float | None) -> dict | None:
    """{'low': 최저가, 'count': 매칭 글 수, 'last': 마지막 글 날짜, 'sub': 대표 서브레딧} 또는 None"""
    q, must = query_for(brand, title)
    if len(q) < 4 or not must:
        return None
    if q in _cache:
        return _cache[q]
    if _off["v"]:
        return None
    STATS["queried"] += 1
    STATS["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        posts = _search(q)
    except Exception as e:
        STATS["errors"] += 1
        STATS["last_error"] = f"{type(e).__name__} {getattr(e, 'code', '')}".strip()
        if getattr(e, "code", 0) in (401, 403, 429):
            _off["v"] = True
        _cache[q] = None
        return None
    time.sleep(1.0)        # 레딧 호출 간격
    hits = []
    for p in posts:
        sub = (p.get("subreddit") or "").lower()
        ttl = p.get("title") or ""
        if sub not in DEAL_SUBS or not all(m in ttl.lower() for m in must):
            continue
        cands = [float(x.replace(",", "")) for x in _PRICE.findall(ttl)]
        if price:
            cands = [c for c in cands if price * 0.3 <= c <= price * 4]
        if cands:
            hits.append((cands[0], p.get("created_utc") or 0, sub))
    if not hits:
        _cache[q] = None
        return None
    low = min(h[0] for h in hits)
    last = max(h[1] for h in hits)
    res = {"low": round(low, 2), "count": len(hits),
           "last": datetime.fromtimestamp(last, timezone.utc).strftime("%Y-%m") if last else "",
           "sub": max({h[2] for h in hits}, key=lambda s: sum(1 for h in hits if h[2] == s))}
    STATS["found"] += 1
    _cache[q] = res
    return res


def annotate(deals: list, limit: int = MAX_LOOKUPS) -> int:
    """점수 높은 후보만 조회해 deal.signals['reddit_*'] 를 채운다. 채운 개수를 돌려준다."""
    n = 0
    for d in sorted(deals, key=lambda x: -(getattr(x, "score", 0) or 0))[:limit]:
        if not getattr(d, "price_current", None):
            continue
        r = lookup(getattr(d, "brand", "") or "", getattr(d, "title", "") or "", d.price_current)
        if r:
            d.signals["reddit_low"] = r["low"]
            d.signals["reddit_count"] = r["count"]
            d.signals["reddit_last"] = r["last"]
            d.signals["reddit_sub"] = r["sub"]
            n += 1
    return n
