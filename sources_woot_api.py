"""
sources_woot_api.py — Woot 공식 Developer API로 직접 수집

기존까지는 Woot이 React Native Web이라 CSS 클래스가 난독화(css-175oi2r r-e5yqq3)돼
있어 헤드리스 브라우저(renderer.py)로 렌더링한 뒤 정규식(parsers.py의 woot()/_WT_*)
으로 긁어냈다. 느리고(사이트당 수 초) 사이트 구조가 바뀌면 바로 깨지는 방식이었는데,
2026-10 forums.woot.com "Request Developer API Key" 스레드에 댓글로 신청해 공식
API 키를 발급받아(승인 후 포럼 쪽지로 전달 — 이메일 아님) JSON으로 직접 받는
방식으로 교체한다.

사용법:
    1) setup_woot.py 실행 (또는 16_WootAPI설정.bat) → WOOT_API_KEY 영구 저장
    2) 이 모듈은 그 환경변수를 읽어 자동으로 활성화된다. 키가 없으면 빈 리스트를
       반환하고, sources.py의 fetch_watchlist()가 기존 렌더링 경로로 Woot을 계속
       커버한다 — 즉 이 모듈은 "있으면 쓰고 없으면 조용히 안 쓰는" 선택적 경로다.

API 레퍼런스(developer.woot.com):
    - 인증: 모든 요청에 헤더 `x-api-key: <키>` (OPTIONS 제외)
    - GET  /feed/{feedname}?page=N   → 현재 진행 중인 딜 요약 목록(100건/페이지)
    - 레이트리밋: 평균 1req/s, 버스트 10, 일일 1,000건(캐시 응답은 한도 제외)

한 offer 안에 variant(색상/사이즈)가 여러 개 있어도 Woot 페이지 URL은 하나뿐이라
offer당 한 행만 만든다 — store.py의 deal_key가 URL 기준이라, variant별로 행을
쪼개면 같은 deal_key끼리 테이블에서 서로 덮어써 알림이 중복/불안정해진다(기존
헤드리스 파서도 카드당 한 행만 뽑던 것과 같은 이유). 피드가 주는 가격범위 중
가장 저렴한 쪽(Minimum)을 대표값으로 쓴다 — 실제 페이지에 들어가면 다른 variant
가격도 다 보이므로 정보 손실은 없다.

우리 호출량: 15분 주기 × feed 1~2회(페이지당 100건) ≈ 하루 150건 내외로
일일 한도 1,000건에 충분히 여유가 있다.
"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request

API_BASE = "https://developer.woot.com"

# "All"이 전 카테고리(Clearance/Electronics/Sellout 등)를 한 번에 담아온다.
# 실측 4년치 Woot 서브도메인 분포(sport 64/electronics 42/computers 40/
# sellout 31/home 20/tools 6)를 한 피드로 커버하려는 목적.
FEED_NAME = "All"
MAX_FEED_PAGES = 3          # 피드 페이지당 최대 100건 — 과호출 방지 안전 상한
REQUEST_TIMEOUT_SEC = 12
MAX_ROWS = 80


def _api_key() -> str:
    return (os.environ.get("WOOT_API_KEY") or "").strip()


def _call(path: str, method: str = "GET", body=None):
    key = _api_key()
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"x-api-key": key, "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_feed() -> list[dict]:
    """현재 진행 중인 딜 요약(FeedItem) 목록. 페이지가 꽉 찼을 때만 다음 페이지로."""
    items: list[dict] = []
    for page in range(1, MAX_FEED_PAGES + 1):
        try:
            res = _call(f"/feed/{FEED_NAME}?page={page}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"[woot_api] feed 호출 실패(page {page}, HTTP {e.code}): {body}")
            break
        except Exception as e:
            print(f"[woot_api] feed 호출 실패(page {page}): {type(e).__name__}: {e}")
            break
        batch = res.get("Items") if isinstance(res, dict) else res
        if not batch:
            break
        items.extend(batch)
        if len(batch) < 100:
            break
        time.sleep(1)  # 평균 1req/s 레이트리밋 여유
    return items


def _brand_or_blank(title: str) -> str:
    """사전에 등록된 실제 브랜드만 인정한다(parsers._known_brand와 동일 원칙).
    못 찾으면 빈 값 — 제목이 대표 표기가 된다."""
    try:
        from parsers import _known_brand
        return _known_brand(title)
    except Exception:
        return ""


def fetch_woot_api() -> list:
    """Deal 리스트. filter_engine을 지연 import해 순환참조를 피한다."""
    if not _api_key():
        return []
    try:
        from filter_engine import Deal
    except ImportError:
        return []

    feed_items = _fetch_feed()
    if not feed_items:
        return []

    rows: list[Deal] = []
    seen_urls: set[str] = set()
    for it in feed_items:
        if it.get("IsSoldOut"):
            continue  # 품절 딜은 발행해봐야 링크가 죽어 있다

        offer_url = it.get("Url") or ""
        if not offer_url or offer_url in seen_urls:
            continue

        cur = (it.get("SalePrice") or {}).get("Minimum")
        lst = (it.get("ListPrice") or {}).get("Minimum")
        if not cur or not lst or lst <= cur:
            continue

        title = (it.get("Title") or "").strip()
        subtitle = (it.get("Subtitle") or "").strip()
        if subtitle and subtitle not in title:
            title = f"{title} — {subtitle}"
        title = title[:140]
        if not title:
            continue

        seen_urls.add(offer_url)
        rows.append(Deal(
            source="woot.com",
            source_tier="T1",
            url=offer_url,
            title=title,
            brand=_brand_or_blank(title),
            image=it.get("Photo") or "",
            price_current=float(cur),
            price_list=float(lst),
            price_baseline=float(lst),
            currency="USD",
            stock_status="out_of_stock" if it.get("IsSoldOut") else "in_stock",
            collection_method="api",
        ))
        if len(rows) >= MAX_ROWS:
            break
    return rows
